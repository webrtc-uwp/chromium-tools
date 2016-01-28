# Copyright 2012 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.


def _CommonChecks(input_api, output_api):
  results = []

  results.extend(input_api.RunTests(input_api.canned_checks.GetPylint(
      input_api, output_api, extra_paths_list=_GetPathsToPrepend(input_api),
      pylintrc='pylintrc')))
  results.extend(_CheckNoMoreUsageOfDeprecatedCode(
    input_api, output_api, deprecated_code='GetChromiumSrcDir()',
    crbug_number=511332))

  results.extend(_TemporarilyReadOnly(input_api, output_api))
  return results


def _TemporarilyReadOnly(input_api, output_api):
  # Temporarily make tools/telemetry/ read-only for the move to catapult.

  def other_files(f):
    this_presubmit_file = input_api.os_path.join(
        input_api.PresubmitLocalPath(), 'PRESUBMIT.py')
    return not f.AbsoluteLocalPath() == this_presubmit_file

  changed_files = input_api.AffectedSourceFiles(other_files)
  if changed_files:
    return [output_api.PresubmitError(
        'tools/telemetry/ has moved to the catapult repo. It is deprecated and '
        'scheduled for deletion on 1/29/16.'
        '\nPlease instead make your changes to telemetry/ '
        'in https://github.com/catapult-project/catapult/tree/master/telemetry.'
        '\nContact aiolos@ for further questions. Changed files:\n',
        items=changed_files)]
  return []


def _RunArgs(args, input_api):
  p = input_api.subprocess.Popen(args, stdout=input_api.subprocess.PIPE,
                                 stderr=input_api.subprocess.STDOUT)
  out, _ = p.communicate()
  return (out, p.returncode)


def _CheckTelemetryBinaryDependencies(input_api, output_api):
  """ Check that binary_dependencies.json has valid format and content.

  This check should only be done in CheckChangeOnUpload() only since it invokes
  network I/O.
  """
  results = []
  telemetry_dir = input_api.PresubmitLocalPath()
  telemetry_binary_dependencies_path = input_api.os_path.join(
      telemetry_dir, 'telemetry', 'internal', 'binary_dependencies.json')
  for f in input_api.AffectedFiles():
    if not f.AbsoluteLocalPath() == telemetry_binary_dependencies_path:
      continue
    out, return_code = _RunArgs([
        input_api.python_executable,
        input_api.os_path.join(telemetry_dir, 'json_format'),
        telemetry_binary_dependencies_path], input_api)
    if return_code:
      results.append(output_api.PresubmitError(
           'Validating binary_dependencies.json failed:', long_text=out))
      break
    out, return_code = _RunArgs([
        input_api.python_executable,
        input_api.os_path.join(telemetry_dir, 'validate_binary_dependencies'),
        telemetry_binary_dependencies_path], input_api)
    if return_code:
      results.append(output_api.PresubmitError(
          'Validating binary_dependencies.json failed:', long_text=out))
      break
  return results


def _CheckNoMoreUsageOfDeprecatedCode(
    input_api, output_api, deprecated_code, crbug_number):
  results = []
  # These checks are not perfcet but should be good enough for most of our
  # usecases.
  def _IsAddedLine(line):
    return line.startswith('+') and not line.startswith('+++ ')
  def _IsRemovedLine(line):
    return line.startswith('-') and not line.startswith('--- ')

  presubmit_dir = input_api.os_path.join(
      input_api.PresubmitLocalPath(), 'PRESUBMIT.py')

  added_calls = 0
  removed_calls = 0
  for affected_file in input_api.AffectedFiles():
    # Do not do the check on PRESUBMIT.py itself.
    if affected_file.AbsoluteLocalPath() == presubmit_dir:
      continue
    for line in affected_file.GenerateScmDiff().splitlines():
      if _IsAddedLine(line) and deprecated_code in line:
        added_calls += 1
      elif _IsRemovedLine(line) and deprecated_code in line:
        removed_calls += 1

  if added_calls > removed_calls:
    results.append(output_api.PresubmitError(
        'Your patch adds more instances of %s. Please see crbug.com/%i for'
        'how to proceed.' % (deprecated_code, crbug_number)))
  return results


def _GetPathsToPrepend(input_api):
  telemetry_dir = input_api.PresubmitLocalPath()
  chromium_src_dir = input_api.os_path.join(telemetry_dir, '..', '..')
  return [
      telemetry_dir,
      input_api.os_path.join(telemetry_dir, 'third_party', 'altgraph'),
      input_api.os_path.join(telemetry_dir, 'third_party', 'mock'),
      input_api.os_path.join(telemetry_dir, 'third_party', 'modulegraph'),
      input_api.os_path.join(telemetry_dir, 'third_party', 'pexpect'),
      input_api.os_path.join(telemetry_dir, 'third_party', 'png'),
      input_api.os_path.join(telemetry_dir, 'third_party', 'pyfakefs'),
      input_api.os_path.join(telemetry_dir, 'third_party', 'pyserial'),
      input_api.os_path.join(telemetry_dir, 'third_party', 'typ'),
      input_api.os_path.join(telemetry_dir, 'third_party', 'webpagereplay'),
      input_api.os_path.join(telemetry_dir, 'third_party', 'websocket-client'),

      input_api.os_path.join(chromium_src_dir, 'build', 'android'),
      input_api.os_path.join(chromium_src_dir,
                             'third_party', 'catapult', 'catapult_base'),
      input_api.os_path.join(chromium_src_dir,
                             'third_party', 'catapult', 'dependency_manager'),
      input_api.os_path.join(chromium_src_dir,
                             'third_party', 'catapult', 'tracing'),
  ]


def CheckChangeOnUpload(input_api, output_api):
  results = []
  results.extend(_CommonChecks(input_api, output_api))
  results.extend(_CheckTelemetryBinaryDependencies(input_api, output_api))
  return results


def CheckChangeOnCommit(input_api, output_api):
  results = []
  results.extend(_CommonChecks(input_api, output_api))
  return results
