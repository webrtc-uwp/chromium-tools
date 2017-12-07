#!/usr/bin/python
# Copyright 2017 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""This script helps to generate code coverage report.

  It uses Clang Source-based Code Coverage -
  https://clang.llvm.org/docs/SourceBasedCodeCoverage.html

  In order to generate code coverage report, you need to first add
  "use_clang_coverage=true" GN flag to args.gn file in your build
  output directory (e.g. out/coverage).

  It is recommended to add "is_component_build=false" flag as well because:
  1. It is incompatible with other sanitizer flags (like "is_asan", "is_msan")
     and others like "optimize_for_fuzzing".
  2. If it is not set explicitly, "is_debug" overrides it to true.

  Example usage:

  gn gen out/coverage --args='use_clang_coverage=true is_component_build=false'
  gclient runhooks
  python tools/code_coverage/coverage.py crypto_unittests url_unittests \\
      -b out/coverage -o out/report -c 'out/coverage/crypto_unittests' \\
      -c 'out/coverage/url_unittests --gtest_filter=URLParser.PathURL' \\
      -f url/ -f crypto/

  The command above builds crypto_unittests and url_unittests targets and then
  runs them with specified command line arguments. For url_unittests, it only
  runs the test URLParser.PathURL. The coverage report is filtered to include
  only files and sub-directories under url/ and crypto/ directories.

  If you are building a fuzz target, you need to add "use_libfuzzer=true" GN
  flag as well.

  Sample workflow for a fuzz target (e.g. pdfium_fuzzer):

  python tools/code_coverage/coverage.py pdfium_fuzzer \\
      -b out/coverage -o out/report \\
      -c 'out/coverage/pdfium_fuzzer -runs=<runs> <corpus_dir>' \\
      -f third_party/pdfium

  where:
    <corpus_dir> - directory containing samples files for this format.
    <runs> - number of times to fuzz target function. Should be 0 when you just
             want to see the coverage on corpus and don't want to fuzz at all.

  For more options, please refer to tools/code_coverage/coverage.py -h.
"""

from __future__ import print_function

import sys

import argparse
import os
import subprocess
import threading
import urllib2

sys.path.append(
    os.path.join(
        os.path.dirname(__file__), os.path.pardir, os.path.pardir, 'tools',
        'clang', 'scripts'))

import update as clang_update

# Absolute path to the root of the checkout.
SRC_ROOT_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), os.path.pardir, os.path.pardir))

# Absolute path to the code coverage tools binary.
LLVM_BUILD_DIR = clang_update.LLVM_BUILD_DIR
LLVM_COV_PATH = os.path.join(LLVM_BUILD_DIR, 'bin', 'llvm-cov')
LLVM_PROFDATA_PATH = os.path.join(LLVM_BUILD_DIR, 'bin', 'llvm-profdata')

# Build directory, the value is parsed from command line arguments.
BUILD_DIR = None

# Output directory for generated artifacts, the value is parsed from command
# line arguemnts.
OUTPUT_DIR = None

# Default number of jobs used to build when goma is configured and enabled.
DEFAULT_GOMA_JOBS = 100

# Name of the file extension for profraw data files.
PROFRAW_FILE_EXTENSION = 'profraw'

# Name of the final profdata file, and this file needs to be passed to
# "llvm-cov" command in order to call "llvm-cov show" to inspect the
# line-by-line coverage of specific files.
PROFDATA_FILE_NAME = 'coverage.profdata'

# Build arg required for generating code coverage data.
CLANG_COVERAGE_BUILD_ARG = 'use_clang_coverage'

# A set of targets that depend on target "testing/gtest", this set is generated
# by 'gn refs "testing/gtest"', and it is lazily initialized when needed.
GTEST_TARGET_NAMES = None


def _GetPlatform():
  """Returns current running platform."""
  if sys.platform == 'win32' or sys.platform == 'cygwin':
    return 'win'
  if sys.platform.startswith('linux'):
    return 'linux'
  else:
    assert sys.platform == 'darwin'
    return 'mac'


# TODO(crbug.com/759794): remove this function once tools get included to
# Clang bundle:
# https://chromium-review.googlesource.com/c/chromium/src/+/688221
def DownloadCoverageToolsIfNeeded():
  """Temporary solution to download llvm-profdata and llvm-cov tools."""

  def _GetRevisionFromStampFile(stamp_file_path, platform):
    """Returns a pair of revision number by reading the build stamp file.

    Args:
      stamp_file_path: A path the build stamp file created by
                       tools/clang/scripts/update.py.
    Returns:
      A pair of integers represeting the main and sub revision respectively.
    """
    if not os.path.exists(stamp_file_path):
      return 0, 0

    with open(stamp_file_path) as stamp_file:
      for stamp_file_line in stamp_file.readlines():
        if ',' in stamp_file_line:
          package_version, target_os = stamp_file_line.rstrip().split(',')
        else:
          package_version = stamp_file_line.rstrip()
          target_os = ''

        if target_os and platform != target_os:
          continue

        clang_revision_str, clang_sub_revision_str = package_version.split('-')
        return int(clang_revision_str), int(clang_sub_revision_str)

    assert False, 'Coverage is only supported on target_os - linux, mac.'

  platform = _GetPlatform()
  clang_revision, clang_sub_revision = _GetRevisionFromStampFile(
      clang_update.STAMP_FILE, platform)

  coverage_revision_stamp_file = os.path.join(
      os.path.dirname(clang_update.STAMP_FILE), 'cr_coverage_revision')
  coverage_revision, coverage_sub_revision = _GetRevisionFromStampFile(
      coverage_revision_stamp_file, platform)

  has_coverage_tools = (os.path.exists(LLVM_COV_PATH) and
                        os.path.exists(LLVM_PROFDATA_PATH))

  if (has_coverage_tools and
      coverage_revision == clang_revision and
      coverage_sub_revision == clang_sub_revision):
    # LLVM coverage tools are up to date, bail out.
    return clang_revision

  package_version = '%d-%d' % (clang_revision, clang_sub_revision)
  coverage_tools_file = 'llvm-code-coverage-%s.tgz' % package_version

  # The code bellow follows the code from tools/clang/scripts/update.py.
  if platform == 'mac':
    coverage_tools_url = clang_update.CDS_URL + '/Mac/' + coverage_tools_file
  else:
    assert platform == 'linux'
    coverage_tools_url = (
        clang_update.CDS_URL + '/Linux_x64/' + coverage_tools_file)

  try:
    clang_update.DownloadAndUnpack(coverage_tools_url,
                                   clang_update.LLVM_BUILD_DIR)
    print('Coverage tools %s unpacked' % package_version)
    with open(coverage_revision_stamp_file, 'w') as file_handle:
      file_handle.write('%s,%s' % (package_version, platform))
      file_handle.write('\n')
  except urllib2.URLError:
    raise Exception(
        'Failed to download coverage tools: %s.' % coverage_tools_url)


def _GenerateLineByLineFileCoverageInHtml(binary_paths, profdata_file_path,
                                          filters):
  """Generates per file line-by-line coverage in html using 'llvm-cov show'.

  For a file with absolute path /a/b/x.cc, a html report is generated as:
  OUTPUT_DIR/coverage/a/b/x.cc.html. An index html file is also generated as:
  OUTPUT_DIR/index.html.

  Args:
    binary_paths: A list of paths to the instrumented binaries.
    profdata_file_path: A path to the profdata file.
    filters: A list of directories and files to get coverage for.
  """
  print('Generating per file line-by-line code coverage in html '
        '(this can take a while depending on size of target!)')

  # llvm-cov show [options] -instr-profile PROFILE BIN [-object BIN,...]
  # [[-object BIN]] [SOURCES]
  # NOTE: For object files, the first one is specified as a positional argument,
  # and the rest are specified as keyword argument.
  subprocess_cmd = [
      LLVM_COV_PATH, 'show', '-format=html',
      '-output-dir={}'.format(OUTPUT_DIR),
      '-instr-profile={}'.format(profdata_file_path), binary_paths[0]
  ]
  subprocess_cmd.extend(
      ['-object=' + binary_path for binary_path in binary_paths[1:]])
  subprocess_cmd.extend(filters)

  subprocess.check_call(subprocess_cmd)


def _CreateCoverageProfileDataForTargets(targets, commands, jobs_count=None):
  """Builds and runs target to generate the coverage profile data.

  Args:
    targets: A list of targets to build with coverage instrumentation.
    commands: A list of commands used to run the targets.
    jobs_count: Number of jobs to run in parallel for building. If None, a
                default value is derived based on CPUs availability.

  Returns:
    A relative path to the generated profdata file.
  """
  _BuildTargets(targets, jobs_count)
  profraw_file_paths = _GetProfileRawDataPathsByExecutingCommands(
      targets, commands)
  profdata_file_path = _CreateCoverageProfileDataFromProfRawData(
      profraw_file_paths)

  return profdata_file_path


def _BuildTargets(targets, jobs_count):
  """Builds target with Clang coverage instrumentation.

  This function requires current working directory to be the root of checkout.

  Args:
    targets: A list of targets to build with coverage instrumentation.
    jobs_count: Number of jobs to run in parallel for compilation. If None, a
                default value is derived based on CPUs availability.


  """

  def _IsGomaConfigured():
    """Returns True if goma is enabled in the gn build args.

    Returns:
      A boolean indicates whether goma is configured for building or not.
    """
    build_args = _ParseArgsGnFile()
    return 'use_goma' in build_args and build_args['use_goma'] == 'true'

  print('Building %s' % str(targets))

  if jobs_count is None and _IsGomaConfigured():
    jobs_count = DEFAULT_GOMA_JOBS

  subprocess_cmd = ['ninja', '-C', BUILD_DIR]
  if jobs_count is not None:
    subprocess_cmd.append('-j' + str(jobs_count))

  subprocess_cmd.extend(targets)
  subprocess.check_call(subprocess_cmd)


def _GetProfileRawDataPathsByExecutingCommands(targets, commands):
  """Runs commands and returns the relative paths to the profraw data files.

  Args:
    targets: A list of targets built with coverage instrumentation.
    commands: A list of commands used to run the targets.

  Returns:
    A list of relative paths to the generated profraw data files.
  """
  # Remove existing profraw data files.
  for file_or_dir in os.listdir(OUTPUT_DIR):
    if file_or_dir.endswith(PROFRAW_FILE_EXTENSION):
      os.remove(os.path.join(OUTPUT_DIR, file_or_dir))

  # Run different test targets in parallel to generate profraw data files.
  threads = []
  for target, command in zip(targets, commands):
    thread = threading.Thread(target=_ExecuteCommand, args=(target, command))
    thread.start()
    threads.append(thread)
  for thread in threads:
    thread.join()

  profraw_file_paths = []
  for file_or_dir in os.listdir(OUTPUT_DIR):
    if file_or_dir.endswith(PROFRAW_FILE_EXTENSION):
      profraw_file_paths.append(os.path.join(OUTPUT_DIR, file_or_dir))

  # Assert one target/command generates at least one profraw data file.
  for target in targets:
    assert any(
        os.path.basename(profraw_file).startswith(target)
        for profraw_file in profraw_file_paths), (
            'Running target: %s failed to generate any profraw data file, '
            'please make sure the binary exists and is properly instrumented.' %
            target)

  return profraw_file_paths


def _ExecuteCommand(target, command):
  """Runs a single command and generates a profraw data file.

  Args:
    target: A target built with coverage instrumentation.
    command: A command used to run the target.
  """
  if _IsTargetGTestTarget(target):
    # This test argument is required and only required for gtest unit test
    # targets because by default, they run tests in parallel, and that won't
    # generated code coverage data correctly.
    command += ' --test-launcher-jobs=1'

  expected_profraw_file_name = os.extsep.join(
      [target, '%p', PROFRAW_FILE_EXTENSION])
  expected_profraw_file_path = os.path.join(OUTPUT_DIR,
                                            expected_profraw_file_name)
  output_file_name = os.extsep.join([target + '_output', 'txt'])
  output_file_path = os.path.join(OUTPUT_DIR, output_file_name)

  print('Running command: "%s", the output is redirected to "%s"' %
        (command, output_file_path))
  output = subprocess.check_output(
      command.split(), env={
          'LLVM_PROFILE_FILE': expected_profraw_file_path
      })
  with open(output_file_path, 'w') as output_file:
    output_file.write(output)


def _CreateCoverageProfileDataFromProfRawData(profraw_file_paths):
  """Returns a relative path to the profdata file by merging profraw data files.

  Args:
    profraw_file_paths: A list of relative paths to the profraw data files that
                        are to be merged.

  Returns:
    A relative path to the generated profdata file.

  Raises:
    CalledProcessError: An error occurred merging profraw data files.
  """
  print('Creating the profile data file')

  profdata_file_path = os.path.join(OUTPUT_DIR, PROFDATA_FILE_NAME)

  try:
    subprocess_cmd = [
        LLVM_PROFDATA_PATH, 'merge', '-o', profdata_file_path, '-sparse=true'
    ]
    subprocess_cmd.extend(profraw_file_paths)
    subprocess.check_call(subprocess_cmd)
  except subprocess.CalledProcessError as error:
    print('Failed to merge profraw files to create profdata file')
    raise error

  return profdata_file_path


def _GetBinaryPath(command):
  """Returns a relative path to the binary to be run by the command.

  Args:
    command: A command used to run a target.

  Returns:
    A relative path to the binary.
  """
  return command.split()[0]


def _IsTargetGTestTarget(target):
  """Returns True if the target is a gtest target.

  Args:
    target: A target built with coverage instrumentation.

  Returns:
    A boolean value indicates whether the target is a gtest target.
  """
  global GTEST_TARGET_NAMES
  if GTEST_TARGET_NAMES is None:
    output = subprocess.check_output(['gn', 'refs', BUILD_DIR, 'testing/gtest'])
    list_of_gtest_targets = [
        gtest_target for gtest_target in output.splitlines() if gtest_target
    ]
    GTEST_TARGET_NAMES = set(
        [gtest_target.split(':')[1] for gtest_target in list_of_gtest_targets])

  return target in GTEST_TARGET_NAMES


def _VerifyTargetExecutablesAreInBuildDirectory(commands):
  """Verifies that the target executables specified in the commands are inside
  the given build directory."""
  for command in commands:
    binary_path = _GetBinaryPath(command)
    binary_absolute_path = os.path.abspath(os.path.normpath(binary_path))
    assert binary_absolute_path.startswith(os.path.abspath(BUILD_DIR)), (
        'Target executable "%s" in command: "%s" is outside of '
        'the given build directory: "%s".' % (binary_path, command, BUILD_DIR))


def _ValidateBuildingWithClangCoverage():
  """Asserts that targets are built with Clang coverage enabled."""
  build_args = _ParseArgsGnFile()

  if (CLANG_COVERAGE_BUILD_ARG not in build_args or
      build_args[CLANG_COVERAGE_BUILD_ARG] != 'true'):
    assert False, ('\'{} = true\' is required in args.gn.'
                  ).format(CLANG_COVERAGE_BUILD_ARG)


def _ParseArgsGnFile():
  """Parses args.gn file and returns results as a dictionary.

  Returns:
    A dictionary representing the build args.
  """
  build_args_path = os.path.join(BUILD_DIR, 'args.gn')
  assert os.path.exists(build_args_path), ('"%s" is not a build directory, '
                                           'missing args.gn file.' % BUILD_DIR)
  with open(build_args_path) as build_args_file:
    build_args_lines = build_args_file.readlines()

  build_args = {}
  for build_arg_line in build_args_lines:
    build_arg_without_comments = build_arg_line.split('#')[0]
    key_value_pair = build_arg_without_comments.split('=')
    if len(key_value_pair) != 2:
      continue

    key = key_value_pair[0].strip()
    value = key_value_pair[1].strip()
    build_args[key] = value

  return build_args


def _VerifyPathsAndReturnAbsolutes(paths):
  """Verifies that the paths specified in |paths| exist and returns absolute
  versions.

  Args:
    paths: A list of files or directories.
  """
  absolute_paths = []
  for path in paths:
    absolute_path = os.path.join(SRC_ROOT_PATH, path)
    assert os.path.exists(absolute_path), ('Path: "%s" doesn\'t exist.' % path)

    absolute_paths.append(absolute_path)

  return absolute_paths


def _ParseCommandArguments():
  """Adds and parses relevant arguments for tool comands.

  Returns:
    A dictionary representing the arguments.
  """
  arg_parser = argparse.ArgumentParser()
  arg_parser.usage = __doc__

  arg_parser.add_argument(
      '-b',
      '--build-dir',
      type=str,
      required=True,
      help='The build directory, the path needs to be relative to the root of '
      'the checkout.')

  arg_parser.add_argument(
      '-o',
      '--output-dir',
      type=str,
      required=True,
      help='Output directory for generated artifacts.')

  arg_parser.add_argument(
      '-c',
      '--command',
      action='append',
      required=True,
      help='Commands used to run test targets, one test target needs one and '
      'only one command, when specifying commands, one should assume the '
      'current working directory is the root of the checkout.')

  arg_parser.add_argument(
      '-f',
      '--filters',
      action='append',
      required=False,
      help='Directories or files to get code coverage for, and all files under '
      'the directories are included recursively.')

  arg_parser.add_argument(
      '-j',
      '--jobs',
      type=int,
      default=None,
      help='Run N jobs to build in parallel. If not specified, a default value '
      'will be derived based on CPUs availability. Please refer to '
      '\'ninja -h\' for more details.')

  arg_parser.add_argument(
      'targets', nargs='+', help='The names of the test targets to run.')

  args = arg_parser.parse_args()
  return args


def Main():
  """Execute tool commands."""
  assert _GetPlatform() in ['linux', 'mac'], (
      'Coverage is only supported on linux and mac platforms.')
  assert os.path.abspath(os.getcwd()) == SRC_ROOT_PATH, ('This script must be '
                                                         'called from the root '
                                                         'of checkout.')
  DownloadCoverageToolsIfNeeded()

  args = _ParseCommandArguments()
  global BUILD_DIR
  BUILD_DIR = args.build_dir
  global OUTPUT_DIR
  OUTPUT_DIR = args.output_dir

  assert len(args.targets) == len(args.command), ('Number of targets must be '
                                                  'equal to the number of test '
                                                  'commands.')
  assert os.path.exists(BUILD_DIR), (
      'Build directory: {} doesn\'t exist. '
      'Please run "gn gen" to generate.').format(BUILD_DIR)
  _ValidateBuildingWithClangCoverage()
  _VerifyTargetExecutablesAreInBuildDirectory(args.command)

  absolute_filter_paths = []
  if args.filters:
    absolute_filter_paths = _VerifyPathsAndReturnAbsolutes(args.filters)

  if not os.path.exists(OUTPUT_DIR):
    os.makedirs(OUTPUT_DIR)

  profdata_file_path = _CreateCoverageProfileDataForTargets(
      args.targets, args.command, args.jobs)

  binary_paths = [_GetBinaryPath(command) for command in args.command]
  _GenerateLineByLineFileCoverageInHtml(binary_paths, profdata_file_path,
                                        absolute_filter_paths)
  html_index_file_path = 'file://' + os.path.abspath(
      os.path.join(OUTPUT_DIR, 'index.html'))
  print('\nCode coverage profile data is created as: %s' % profdata_file_path)
  print('Index file for html report is generated as: %s' % html_index_file_path)


if __name__ == '__main__':
  sys.exit(Main())
