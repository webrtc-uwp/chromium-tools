// Copyright (c) 2012 The Chromium Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#ifndef TOOLS_ANDROID_COMMON_NET_H_
#define TOOLS_ANDROID_COMMON_NET_H_
#pragma once

namespace tools {

// DisableNagle can improve TCP transmission performance. Both Chrome net stack
// and adb tool use it.
int DisableNagle(int socket);

// Wake up listener only when data arrive.
int DeferAccept(int socket);

}  // namespace tools

#endif  // TOOLS_ANDROID_COMMON_NET_H_

