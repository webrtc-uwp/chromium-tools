# Copyright (c) 2013 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

from metrics import smoothness
from telemetry.page import page_measurement

class Smoothness(page_measurement.PageMeasurement):
  def __init__(self):
    super(Smoothness, self).__init__('smoothness')
    self._metric = None

  def CustomizeBrowserOptions(self, options):
    options.AppendExtraBrowserArgs('--enable-gpu-benchmarking')

  def CanRunForPage(self, page):
    return hasattr(page, 'smoothness')

  def WillRunActions(self, page, tab):
    self._metric = smoothness.SmoothnessMetric()
    self._metric.Start(page, tab)

  def DidRunAction(self, page, tab, action):
    self._metric.AddActionToIncludeInMetric(action)

  def DidRunActions(self, page, tab):
    self._metric.Stop(page, tab)

  def MeasurePage(self, page, tab, results):
    self._metric.AddResults(tab, results)
