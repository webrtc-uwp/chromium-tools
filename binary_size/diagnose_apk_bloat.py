#!/usr/bin/env python
# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

"""Tool for finding the cause of APK bloat.

Run diagnose_apk_bloat.py -h for detailed usage help.
"""

import argparse
import collections
from contextlib import contextmanager
import distutils.spawn
import json
import multiprocessing
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile

_COMMIT_COUNT_WARN_THRESHOLD = 15
_ALLOWED_CONSECUTIVE_FAILURES = 2
_BUILDER_URL = \
    'https://build.chromium.org/p/chromium.perf/builders/Android%20Builder'
_CLOUD_OUT_DIR = os.path.join('out', 'Release')
_SRC_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
_DEFAULT_ARCHIVE_DIR = os.path.join(_SRC_ROOT, 'binary-size-bloat')
_DEFAULT_OUT_DIR = os.path.join(_SRC_ROOT, 'out', 'diagnose-apk-bloat')
_DEFAULT_TARGET = 'monochrome_public_apk'


_global_restore_checkout_func = None


def _SetRestoreFunc(subrepo):
  branch = _GitCmd(['rev-parse', '--abbrev-ref', 'HEAD'], subrepo)
  global _global_restore_checkout_func
  _global_restore_checkout_func = lambda: _GitCmd(['checkout', branch], subrepo)


class BaseDiff(object):
  """Base class capturing binary size diffs."""
  def __init__(self, name):
    self.name = name
    self.banner = '\n' + '*' * 30 + name + '*' * 30

  def AppendResults(self, logfile):
    """Print and write diff results to an open |logfile|."""
    _PrintAndWriteToFile(logfile, self.banner)
    _PrintAndWriteToFile(logfile, 'Summary:')
    _PrintAndWriteToFile(logfile, self.Summary())
    _PrintAndWriteToFile(logfile, '\nDetails:')
    for l in self.DetailedResults():
      _PrintAndWriteToFile(logfile, l)

  def Summary(self):
    """A short description that summarizes the source of binary size bloat."""
    raise NotImplementedError()

  def DetailedResults(self):
    """An iterable description of the cause of binary size bloat."""
    raise NotImplementedError()

  def ProduceDiff(self, archive_dirs):
    """Prepare a binary size diff with ready to print results."""
    raise NotImplementedError()

  def RunDiff(self, logfile, archive_dirs):
    _Print('Creating {}', self.name)
    self.ProduceDiff(archive_dirs)
    self.AppendResults(logfile)


_ResourceSizesDiffResult = collections.namedtuple(
    'ResourceSizesDiffResult', ['section', 'value', 'units'])


class ResourceSizesDiff(BaseDiff):
  _RESOURCE_SIZES_PATH = os.path.join(
      _SRC_ROOT, 'build', 'android', 'resource_sizes.py')

  def __init__(self, apk_name, slow_options=False):
    self._apk_name = apk_name
    self._slow_options = slow_options
    self._diff = None  # Set by |ProduceDiff()|
    super(ResourceSizesDiff, self).__init__('Resource Sizes Diff')

  def DetailedResults(self):
    for section, value, units in self._diff:
      yield '{:>+10,} {} {}'.format(value, units, section)

  def Summary(self):
    for s in self._diff:
      if 'normalized' in s.section:
        return 'Normalized APK size: {:+,} {}'.format(s.value, s.units)
    return ''

  def ProduceDiff(self, archive_dirs):
    chartjsons = self._RunResourceSizes(archive_dirs)
    diff = []
    with_patch = chartjsons[0]['charts']
    without_patch = chartjsons[1]['charts']
    for section, section_dict in with_patch.iteritems():
      for subsection, v in section_dict.iteritems():
        # Ignore entries when resource_sizes.py chartjson format has changed.
        if (section not in without_patch or
            subsection not in without_patch[section] or
            v['units'] != without_patch[section][subsection]['units']):
          _Print('Found differing dict structures for resource_sizes.py, '
                 'skipping {} {}', section, subsection)
        else:
          diff.append(
              _ResourceSizesDiffResult(
                  '%s %s' % (section, subsection),
                  v['value'] - without_patch[section][subsection]['value'],
                  v['units']))
    self._diff = sorted(diff, key=lambda x: abs(x.value), reverse=True)

  def _RunResourceSizes(self, archive_dirs):
    chartjsons = []
    for archive_dir in archive_dirs:
      apk_path = os.path.join(archive_dir, self._apk_name)
      chartjson_file = os.path.join(archive_dir, 'results-chart.json')
      cmd = [self._RESOURCE_SIZES_PATH, apk_path,'--output-dir', archive_dir,
             '--no-output-dir', '--chartjson']
      if self._slow_options:
        cmd += ['--estimate-patch-size']
      else:
        cmd += ['--no-static-initializer-check']
      _RunCmd(cmd)
      with open(chartjson_file) as f:
        chartjsons.append(json.load(f))
    return chartjsons


class _BuildHelper(object):
  """Helper class for generating and building targets."""
  def __init__(self, args):
    self.cloud = args.cloud
    self.enable_chrome_android_internal = args.enable_chrome_android_internal
    self.extra_gn_args_str = ''
    self.max_jobs = args.max_jobs
    self.max_load_average = args.max_load_average
    self.output_directory = args.output_directory
    self.target = args.target
    self.target_os = args.target_os
    self.use_goma = args.use_goma
    self._SetDefaults()

  @property
  def abs_apk_path(self):
    return os.path.join(self.output_directory, self.apk_path)

  @property
  def apk_name(self):
    # Only works on apk targets that follow: my_great_apk naming convention.
    apk_name = ''.join(s.title() for s in self.target.split('_')[:-1]) + '.apk'
    return apk_name.replace('Webview', 'WebView')

  @property
  def apk_path(self):
    return os.path.join('apks', self.apk_name)

  @property
  def main_lib_name(self):
    # TODO(estevenson): Get this from GN instead of hardcoding.
    if self.IsLinux():
      return 'chrome'
    elif 'monochrome' in self.target:
      return 'lib.unstripped/libmonochrome.so'
    else:
      return 'lib.unstripped/libchrome.so'

  @property
  def main_lib_path(self):
    return os.path.join(self.output_directory, self.main_lib_name)

  @property
  def map_file_name(self):
    return self.main_lib_name + '.map.gz'

  def _SetDefaults(self):
    has_goma_dir = os.path.exists(os.path.join(os.path.expanduser('~'), 'goma'))
    self.use_goma = self.use_goma or has_goma_dir
    self.max_load_average = (self.max_load_average or
                             str(multiprocessing.cpu_count()))
    if not self.max_jobs:
      self.max_jobs = '10000' if self.use_goma else '500'

    if os.path.exists(os.path.join(os.path.dirname(_SRC_ROOT), 'src-internal')):
      self.extra_gn_args_str = ' is_chrome_branded=true'
    else:
      self.extra_gn_args_str = (' exclude_unwind_tables=true '
          'ffmpeg_branding="Chrome" proprietary_codecs=true')

  def _GenGnCmd(self):
    gn_args = 'is_official_build=true symbol_level=1'
    gn_args += ' use_goma=%s' % str(self.use_goma).lower()
    gn_args += ' target_os="%s"' % self.target_os
    gn_args += (' enable_chrome_android_internal=%s' %
                str(self.enable_chrome_android_internal).lower())
    gn_args += self.extra_gn_args_str
    return ['gn', 'gen', self.output_directory, '--args=%s' % gn_args]

  def _GenNinjaCmd(self):
    cmd = ['ninja', '-C', self.output_directory]
    cmd += ['-j', self.max_jobs] if self.max_jobs else []
    cmd += ['-l', self.max_load_average] if self.max_load_average else []
    cmd += [self.target]
    return cmd

  def Run(self):
    """Run GN gen/ninja build and return the process returncode."""
    _Print('Building: {}.', self.target)
    retcode = _RunCmd(
        self._GenGnCmd(), print_stdout=True, exit_on_failure=False)[1]
    if retcode:
      return retcode
    return _RunCmd(
        self._GenNinjaCmd(), print_stdout=True, exit_on_failure=False)[1]

  def IsAndroid(self):
    return self.target_os == 'android'

  def IsLinux(self):
    return self.target_os == 'linux'

  def IsCloud(self):
    return self.cloud


class _BuildArchive(object):
  """Class for managing a directory with build results and build metadata."""
  def __init__(self, rev, base_archive_dir, build, subrepo):
    self.build = build
    self.dir = os.path.join(base_archive_dir, rev)
    metadata_path = os.path.join(self.dir, 'metadata.txt')
    self.rev = rev
    self.metadata = _GenerateMetadata([self], build, metadata_path, subrepo)

  def ArchiveBuildResults(self, bs_dir):
    """Save build artifacts necessary for diffing."""
    _Print('Saving build results to: {}', self.dir)
    _EnsureDirsExist(self.dir)
    build = self.build
    self._ArchiveFile(build.main_lib_path)
    lib_name_noext = os.path.splitext(os.path.basename(build.main_lib_path))[0]
    size_path = os.path.join(self.dir, lib_name_noext + '.size')
    supersize_path = os.path.join(bs_dir, 'supersize')
    tool_prefix = _FindToolPrefix(build.output_directory)
    supersize_cmd = [supersize_path, 'archive', size_path, '--elf-file',
                     build.main_lib_path, '--tool-prefix', tool_prefix,
                     '--output-directory', build.output_directory,
                     '--no-source-paths']
    if build.IsAndroid():
      supersize_cmd += ['--apk-file', build.abs_apk_path]
      self._ArchiveFile(build.abs_apk_path)

    _RunCmd(supersize_cmd)
    _WriteMetadata(self.metadata)

  def Exists(self):
    return _MetadataExists(self.metadata)

  def _ArchiveFile(self, filename):
    if not os.path.exists(filename):
      _Die('missing expected file: {}', filename)
    shutil.copy(filename, self.dir)


class _DiffArchiveManager(object):
  """Class for maintaining BuildArchives and their related diff artifacts."""
  def __init__(self, revs, archive_dir, diffs, build, subrepo):
    self.archive_dir = archive_dir
    self.build = build
    self.build_archives = [_BuildArchive(rev, archive_dir, build, subrepo)
                           for rev in revs]
    self.diffs = diffs
    self.subrepo = subrepo

  def IterArchives(self):
    return iter(self.build_archives)

  def MaybeDiff(self, first_id, second_id):
    """Perform diffs given two build archives."""
    archives = [
        self.build_archives[first_id], self.build_archives[second_id]]
    diff_path = self._DiffFilePath(archives)
    if not self._CanDiff(archives):
      _Print('Skipping diff for {} due to missing build archives.', diff_path)
      return

    metadata_path = self._DiffMetadataPath(archives)
    metadata = _GenerateMetadata(
        archives, self.build, metadata_path, self.subrepo)
    if _MetadataExists(metadata):
      _Print('Skipping diff for {} and {}. Matching diff already exists: {}',
             archives[0].rev, archives[1].rev, diff_path)
    else:
      archive_dirs = [archives[0].dir, archives[1].dir]
      with open(diff_path, 'a') as diff_file:
        for d in self.diffs:
          d.RunDiff(diff_file, archive_dirs)
      _WriteMetadata(metadata)

  def _CanDiff(self, archives):
    return all(a.Exists() for a in archives)

  def _DiffFilePath(self, archives):
    return os.path.join(self._DiffDir(archives), 'diff_results.txt')

  def _DiffMetadataPath(self, archives):
    return os.path.join(self._DiffDir(archives), 'metadata.txt')

  def _DiffDir(self, archives):
    diff_path = os.path.join(
        self.archive_dir, 'diffs', '_'.join(a.rev for a in archives))
    _EnsureDirsExist(diff_path)
    return diff_path


def _EnsureDirsExist(path):
  if not os.path.exists(path):
    os.makedirs(path)


def _GenerateMetadata(archives, build, path, subrepo):
  return {
    'revs': [a.rev for a in archives],
    'archive_dirs': [a.dir for a in archives],
    'target': build.target,
    'target_os': build.target_os,
    'is_cloud': build.IsCloud(),
    'subrepo': subrepo,
    'path': path,
    'gn_args': {
      'extra_gn_args_str': build.extra_gn_args_str,
      'enable_chrome_android_internal': build.enable_chrome_android_internal,
    }
  }


def _WriteMetadata(metadata):
  with open(metadata['path'], 'w') as f:
    json.dump(metadata, f)


def _MetadataExists(metadata):
  old_metadata = {}
  path = metadata['path']
  if os.path.exists(path):
    with open(path, 'r') as f:
      old_metadata = json.load(f)
      ret = len(metadata) == len(old_metadata)
      ret &= all(v == old_metadata[k]
                 for k, v in metadata.items() if k != 'gn_args')
      # GN args don't matter when artifacts are downloaded. For local builds
      # they need to be the same so that diffs are accurate (differing GN args
      # will change the final APK/native library).
      if not metadata['is_cloud']:
        ret &= metadata['gn_args'] == old_metadata['gn_args']
      return ret
  return False


def _RunCmd(cmd, print_stdout=False, exit_on_failure=True):
  """Convenience function for running commands.

  Args:
    cmd: the command to run.
    print_stdout: if this is True, then the stdout of the process will be
        printed instead of returned.
    exit_on_failure: die if an error occurs when this is True.

  Returns:
    Tuple of (process stdout, process returncode).
  """
  cmd_str = ' '.join(c for c in cmd)
  _Print('Running: {}', cmd_str)
  proc_stdout = sys.stdout if print_stdout else subprocess.PIPE

  proc = subprocess.Popen(cmd, stdout=proc_stdout, stderr=subprocess.PIPE)
  stdout, stderr = proc.communicate()

  if proc.returncode and exit_on_failure:
    _Die('command failed: {}\nstderr:\n{}', cmd_str, stderr)

  stdout = stdout.strip() if stdout else ''
  return stdout, proc.returncode


def _GitCmd(args, subrepo):
  return _RunCmd(['git', '-C', subrepo] + args)[0]


def _GclientSyncCmd(rev, subrepo):
  cwd = os.getcwd()
  os.chdir(subrepo)
  _RunCmd(['gclient', 'sync', '-r', 'src@' + rev], print_stdout=True)
  os.chdir(cwd)


def _FindToolPrefix(output_directory):
  build_vars_path = os.path.join(output_directory, 'build_vars.txt')
  if os.path.exists(build_vars_path):
    with open(build_vars_path) as f:
      build_vars = dict(l.rstrip().split('=', 1) for l in f if '=' in l)
    # Tool prefix is relative to output dir, rebase to source root.
    tool_prefix = build_vars['android_tool_prefix']
    while os.path.sep in tool_prefix:
      rebased_tool_prefix = os.path.join(_SRC_ROOT, tool_prefix)
      if os.path.exists(rebased_tool_prefix + 'readelf'):
        return rebased_tool_prefix
      tool_prefix = tool_prefix[tool_prefix.find(os.path.sep) + 1:]
  return ''


def _SyncAndBuild(archive, build, subrepo):
  # Simply do a checkout if subrepo is used.
  if subrepo != _SRC_ROOT:
    _GitCmd(['checkout',  archive.rev], subrepo)
  else:
    # Move to a detached state since gclient sync doesn't work with local
    # commits on a branch.
    _GitCmd(['checkout', '--detach'], subrepo)
    _GclientSyncCmd(archive.rev, subrepo)
  retcode = build.Run()
  return retcode == 0


def _GenerateRevList(with_patch, without_patch, all_in_range, subrepo):
  """Normalize and optionally generate a list of commits in the given range.

  Returns a list of revisions ordered from newest to oldest.
  """
  cmd = ['git', '-C', subrepo, 'merge-base', '--is-ancestor', without_patch,
         with_patch]
  _, retcode = _RunCmd(cmd, exit_on_failure=False)
  assert not retcode and with_patch != without_patch, (
      'Invalid revision arguments, rev_without_patch (%s) is newer than '
      'rev_with_patch (%s)' % (without_patch, with_patch))

  rev_seq = '%s^..%s' % (without_patch, with_patch)
  stdout = _GitCmd(['rev-list', rev_seq], subrepo)
  all_revs = stdout.splitlines()
  if all_in_range:
    revs = all_revs
  else:
    revs = [all_revs[0], all_revs[-1]]
  _VerifyUserAckCommitCount(len(revs))
  return revs


def _VerifyUserAckCommitCount(count):
  if count >= _COMMIT_COUNT_WARN_THRESHOLD:
    _Print('You\'ve provided a commit range that contains {} commits, do you '
           'want to proceed? [y/n]', count)
    if raw_input('> ').lower() != 'y':
      _global_restore_checkout_func()
      sys.exit(1)


def _EnsureDirectoryClean(subrepo):
  _Print('Checking source directory')
  stdout = _GitCmd(['status', '--porcelain'], subrepo)
  # Ignore untracked files.
  if stdout and stdout[:2] != '??':
    _Print('Failure: please ensure working directory is clean.')
    sys.exit()


def _Die(s, *args, **kwargs):
  _Print('Failure: ' + s, *args, **kwargs)
  _global_restore_checkout_func()
  sys.exit(1)


def _DownloadBuildArtifacts(archive, build, bs_dir, depot_tools_path):
  """Download artifacts from arm32 chromium perf builder."""
  if depot_tools_path:
    gsutil_path = os.path.join(depot_tools_path, 'gsutil.py')
  else:
    gsutil_path = distutils.spawn.find_executable('gsutil.py')

  if not gsutil_path:
    _Die('gsutil.py not found, please provide path to depot_tools via '
         '--depot-tools-path or add it to your PATH')

  download_dir = tempfile.mkdtemp(dir=_SRC_ROOT)
  try:
    _DownloadAndArchive(gsutil_path, archive, download_dir, build, bs_dir)
  finally:
    shutil.rmtree(download_dir)


def _DownloadAndArchive(gsutil_path, archive, dl_dir, build, bs_dir):
  dl_file = 'full-build-linux_%s.zip' % archive.rev
  dl_url = 'gs://chrome-perf/Android Builder/%s' % dl_file
  dl_dst = os.path.join(dl_dir, dl_file)
  _Print('Downloading build artifacts for {}', archive.rev)
  # gsutil writes stdout and stderr to stderr, so pipe stdout and stderr to
  # sys.stdout.
  retcode = subprocess.call([gsutil_path, 'cp', dl_url, dl_dir],
                             stdout=sys.stdout, stderr=subprocess.STDOUT)
  if retcode:
      _Die('unexpected error while downloading {}. It may no longer exist on '
           'the server or it may not have been uploaded yet (check {}). '
           'Otherwise, you may not have the correct access permissions.',
           dl_url, _BUILDER_URL)

  # Files needed for supersize and resource_sizes. Paths relative to out dir.
  to_extract = [build.main_lib_name, build.map_file_name, 'args.gn',
                'build_vars.txt', build.apk_path]
  extract_dir = os.path.join(os.path.splitext(dl_dst)[0], 'unzipped')
  # Storage bucket stores entire output directory including out/Release prefix.
  _Print('Extracting build artifacts')
  with zipfile.ZipFile(dl_dst, 'r') as z:
    _ExtractFiles(to_extract, _CLOUD_OUT_DIR, extract_dir, z)
    dl_out = os.path.join(extract_dir, _CLOUD_OUT_DIR)
    build.output_directory, output_directory = dl_out, build.output_directory
    archive.ArchiveBuildResults(bs_dir)
    build.output_directory = output_directory


def _ExtractFiles(to_extract, prefix, dst, z):
  zip_infos = z.infolist()
  assert all(info.filename.startswith(prefix) for info in zip_infos), (
      'Storage bucket folder structure doesn\'t start with %s' % prefix)
  to_extract = [os.path.join(prefix, f) for f in to_extract]
  for f in to_extract:
    z.extract(f, path=dst)


def _Print(s, *args, **kwargs):
  print s.format(*args, **kwargs)


def _PrintAndWriteToFile(logfile, s):
  """Print |s| to |logfile| and stdout."""
  _Print(s)
  logfile.write('%s\n' % s)


@contextmanager
def _TmpBinarySizeDir():
  """Recursively copy files to a temp dir and yield the tmp binary_size dir."""
  # Needs to be at same level of nesting as the real //tools/binary_size
  # since supersize uses this to find d3 in //third_party.
  tmp_dir = tempfile.mkdtemp(dir=_SRC_ROOT)
  try:
    bs_dir = os.path.join(tmp_dir, 'binary_size')
    shutil.copytree(os.path.join(_SRC_ROOT, 'tools', 'binary_size'), bs_dir)
    yield bs_dir
  finally:
    shutil.rmtree(tmp_dir)


def main():
  parser = argparse.ArgumentParser(
      description='Find the cause of APK size bloat.')
  parser.add_argument('--archive-directory',
                      default=_DEFAULT_ARCHIVE_DIR,
                      help='Where results are stored.')
  parser.add_argument('rev',
                      help='Find binary size bloat for this commit.')
  parser.add_argument('--reference-rev',
                      help='Older rev to diff against. If not supplied, '
                      'the previous commit to rev will be used.')
  parser.add_argument('--all',
                      action='store_true',
                      help='Build/download all revs from --reference-rev to '
                      'rev and diff the contiguous revisions.')
  parser.add_argument('--include-slow-options',
                      action='store_true',
                      help='Run some extra steps that take longer to complete. '
                      'This includes apk-patch-size estimation and '
                      'static-initializer counting.')
  parser.add_argument('--cloud',
                      action='store_true',
                      help='Download build artifacts from perf builders '
                      '(Android only, Googlers only).')
  parser.add_argument('--depot-tools-path',
                      help='Custom path to depot tools. Needed for --cloud if '
                      'depot tools isn\'t in your PATH.')
  parser.add_argument('--subrepo',
                      help='Specify a subrepo directory to use. Gclient sync '
                      'will be skipped if this option is used and all git '
                      'commands will be executed from the subrepo directory. '
                      'This option doesn\'t work with --cloud.')

  build_group = parser.add_argument_group('ninja', 'Args to use with ninja/gn')
  build_group.add_argument('-j',
                           dest='max_jobs',
                           help='Run N jobs in parallel.')
  build_group.add_argument('-l',
                           dest='max_load_average',
                           help='Do not start new jobs if the load average is '
                           'greater than N.')
  build_group.add_argument('--no-goma',
                           action='store_false',
                           dest='use_goma',
                           default=True,
                           help='Do not use goma when building with ninja.')
  build_group.add_argument('--target-os',
                           default='android',
                           choices=['android', 'linux'],
                           help='target_os gn arg. Default: android.')
  build_group.add_argument('--output-directory',
                           default=_DEFAULT_OUT_DIR,
                           help='ninja output directory. '
                           'Default: %s.' % _DEFAULT_OUT_DIR)
  build_group.add_argument('--enable-chrome-android-internal',
                           action='store_true',
                           help='Allow downstream targets to be built.')
  build_group.add_argument('--target',
                           default=_DEFAULT_TARGET,
                           help='GN APK target to build. '
                           'Default %s.' % _DEFAULT_TARGET)
  if len(sys.argv) == 1:
    parser.print_help()
    sys.exit()
  args = parser.parse_args()
  build = _BuildHelper(args)
  if build.IsCloud():
    if build.IsLinux():
      parser.error('--cloud only works for android')
    if args.subrepo:
      parser.error('--subrepo doesn\'t work with --cloud')

  subrepo = args.subrepo or _SRC_ROOT
  _EnsureDirectoryClean(subrepo)
  _SetRestoreFunc(subrepo)
  revs = _GenerateRevList(args.rev,
                          args.reference_rev or args.rev + '^',
                          args.all,
                          subrepo)
  diffs = []
  if build.IsAndroid():
    diffs +=  [
        ResourceSizesDiff(
            build.apk_name, slow_options=args.include_slow_options)
    ]
  diff_mngr = _DiffArchiveManager(
      revs, args.archive_directory, diffs, build, subrepo)
  consecutive_failures = 0
  with _TmpBinarySizeDir() as bs_dir:
    for i, archive in enumerate(diff_mngr.IterArchives()):
      if archive.Exists():
        _Print('Found matching metadata for {}, skipping build step.',
               archive.rev)
      else:
        if build.IsCloud():
          _DownloadBuildArtifacts(archive, build, bs_dir, args.depot_tools_path)
        else:
          build_success = _SyncAndBuild(archive, build, subrepo)
          if not build_success:
            consecutive_failures += 1
            if consecutive_failures > _ALLOWED_CONSECUTIVE_FAILURES:
              _Die('{} builds failed in a row, last failure was {}.',
                   consecutive_failures, archive.rev)
          else:
            archive.ArchiveBuildResults(bs_dir)
            consecutive_failures = 0

      if i != 0:
        diff_mngr.MaybeDiff(i - 1, i)

  _global_restore_checkout_func()

if __name__ == '__main__':
  sys.exit(main())

