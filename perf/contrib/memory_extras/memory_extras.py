# Copyright 2015 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from benchmarks import memory
import page_sets

from telemetry import benchmark
from telemetry import story


# pylint: disable=protected-access
@benchmark.Owner(emails=['perezju@chromium.org'])
class DualBrowserBenchmark(memory._MemoryInfra):
  """Measures memory usage while interacting with two different browsers.

  The user story involves going back and forth between doing Google searches
  on a webview-based browser (a stand in for the Search app), and loading
  pages on a select browser.
  """
  page_set = page_sets.DualBrowserStorySet
  options = {'pageset_repeat': 5}

  @classmethod
  def Name(cls):
    return 'memory.dual_browser_test'

  @classmethod
  def ShouldTearDownStateAfterEachStoryRun(cls):
    return False

  @classmethod
  def ValueCanBeAddedPredicate(cls, value, is_first_result):
    # TODO(crbug.com/610962): Remove this stopgap when the perf dashboard
    # is able to cope with the data load generated by TBMv2 metrics.
    return not memory._IGNORED_STATS_RE.search(value.name)

  def GetExpectations(self):
    class StoryExpectations(story.expectations.StoryExpectations):
      def SetExpectations(self):
        pass # Nothing disabled.
    return StoryExpectations()


@benchmark.Owner(emails=['perezju@chromium.org'])
class LongRunningDualBrowserBenchmark(memory._MemoryInfra):
  """Measures memory during prolonged usage of alternating browsers.

  Same as memory.dual_browser_test, but the test is run for 60 iterations
  and the browser is *not* restarted between page set repeats.
  """
  page_set = page_sets.DualBrowserStorySet
  options = {'pageset_repeat': 60}

  @classmethod
  def Name(cls):
    return 'memory.long_running_dual_browser_test'

  @classmethod
  def ShouldTearDownStateAfterEachStoryRun(cls):
    return False

  @classmethod
  def ShouldTearDownStateAfterEachStorySetRun(cls):
    return False

  @classmethod
  def ValueCanBeAddedPredicate(cls, value, is_first_result):
    # TODO(crbug.com/610962): Remove this stopgap when the perf dashboard
    # is able to cope with the data load generated by TBMv2 metrics.
    return not memory._IGNORED_STATS_RE.search(value.name)

  def GetExpectations(self):
    class StoryExpectations(story.expectations.StoryExpectations):
      def SetExpectations(self):
        pass # Nothing disabled.
    return StoryExpectations()


@benchmark.Owner(emails=['etienneb@chromium.org'])
class LongRunningMemoryBenchmarkSitesDesktop(memory._MemoryInfra):
  """Measure memory usage on popular sites.

  This benchmark is intended to run locally over a long period of time. The
  data collected by this benchmark are not metrics but traces with memory dumps.
  The browser process is staying alive for the whole execution and memory dumps
  in these traces can be compare (diff) to determine which objects are potential
  memory leaks.
  """
  options = {
    'pageset_repeat': 30,
    'use_live_sites': True,
    'output_formats': ['json']
  }

  def CreateStorySet(self, options):
    return page_sets.DesktopMemoryPageSet()

  def SetExtraBrowserOptions(self, options):
    super(LongRunningMemoryBenchmarkSitesDesktop, self).SetExtraBrowserOptions(
        options)
    options.AppendExtraBrowserArgs(['--enable-heap-profiling=native'])
    # Disable taking screenshot on failing pages.
    options.take_screenshot_for_failed_page = False

  @classmethod
  def Name(cls):
    return 'memory.long_running_desktop_sites'

  @classmethod
  def ShouldTearDownStateAfterEachStoryRun(cls):
    return False

  @classmethod
  def ShouldTearDownStateAfterEachStorySetRun(cls):
    return False

  @classmethod
  def ValueCanBeAddedPredicate(cls, value, is_first_result):
    # TODO(crbug.com/610962): Remove this stopgap when the perf dashboard
    # is able to cope with the data load generated by TBMv2 metrics.
    return not memory._IGNORED_STATS_RE.search(value.name)

  def GetExpectations(self):
    class StoryExpectations(story.expectations.StoryExpectations):
      def SetExpectations(self):
        pass # Nothing disabled.
    return StoryExpectations()
