# Copyright (c) 2012 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
from chrome_remote_control import multi_page_benchmark
from chrome_remote_control import multi_page_benchmark_unittest_base
from perf_tools import scrolling_benchmark

class ScrollingBenchmarkUnitTest(
  multi_page_benchmark_unittest_base.MultiPageBenchmarkUnitTestBase):

  def testScrollingWithGpuBenchmarkingExtension(self):
    ps = self.CreatePageSetFromFileInUnittestDataDir('scrollable_page.html')

    benchmark = scrolling_benchmark.ScrollingBenchmark()
    all_results = self.RunBenchmark(benchmark, ps)

    self.assertEqual(0, len(all_results.page_failures))
    self.assertEqual(1, len(all_results.page_results))
    results0 = all_results.page_results[0]

    self.assertTrue('dropped_percent' in results0)
    self.assertTrue('mean_frame_time' in results0)

  def testCalcResultsFromRAFRenderStats(self):
    rendering_stats = {'droppedFrameCount': 5,
                       'totalTimeInSeconds': 1,
                       'numAnimationFrames': 10,
                       'numFramesSentToScreen': 10}
    res = multi_page_benchmark.BenchmarkResults()
    res.WillMeasurePage(True)
    scrolling_benchmark.CalcScrollResults(rendering_stats, res)
    res.DidMeasurePage()
    self.assertEquals(50, res.page_results[0]['dropped_percent'])
    self.assertAlmostEquals(100, res.page_results[0]['mean_frame_time'], 2)

  def testCalcResultsRealRenderStats(self):
    rendering_stats = {'numFramesSentToScreen': 60,
                       'globalTotalTextureUploadTimeInSeconds': 0,
                       'totalProcessingCommandsTimeInSeconds': 0,
                       'globalTextureUploadCount': 0,
                       'droppedFrameCount': 0,
                       'textureUploadCount': 0,
                       'numAnimationFrames': 10,
                       'totalPaintTimeInSeconds': 0.35374299999999986,
                       'globalTotalProcessingCommandsTimeInSeconds': 0,
                       'totalTextureUploadTimeInSeconds': 0,
                       'totalRasterizeTimeInSeconds': 0,
                       'totalTimeInSeconds': 1.0}
    res = multi_page_benchmark.BenchmarkResults()
    res.WillMeasurePage(True)
    scrolling_benchmark.CalcScrollResults(rendering_stats, res)
    res.DidMeasurePage()
    self.assertEquals(0, res.page_results[0]['dropped_percent'])
    self.assertAlmostEquals(1000/60., res.page_results[0]['mean_frame_time'], 2)

class ScrollingBenchmarkWithoutGpuBenchmarkingUnitTest(
  multi_page_benchmark_unittest_base.MultiPageBenchmarkUnitTestBase):

  def CustomizeOptionsForTest(self, options):
    options.no_gpu_benchmarking_extension = True

  def testScrollingWithoutGpuBenchmarkingExtension(self):
    ps = self.CreatePageSetFromFileInUnittestDataDir('scrollable_page.html')

    benchmark = scrolling_benchmark.ScrollingBenchmark()
    benchmark.use_gpu_benchmarking_extension = False

    all_results = self.RunBenchmark(benchmark, ps)

    self.assertEqual(0, len(all_results.page_failures))
    self.assertEqual(1, len(all_results.page_results))
    results0 = all_results.page_results[0]

    self.assertTrue('dropped_percent' in results0)
    self.assertTrue('mean_frame_time' in results0)
