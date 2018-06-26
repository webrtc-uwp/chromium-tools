// Copyright 2017 The Chromium Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#include "tools/accessibility/inspect/ax_tree_server.h"

#include <iostream>
#include <string>

#include "base/at_exit.h"
#include "base/bind.h"
#include "base/files/file_util.h"
#include "base/json/json_writer.h"
#include "base/path_service.h"
#include "base/run_loop.h"
#include "base/strings/string_number_conversions.h"
#include "base/strings/string_util.h"
#include "base/strings/utf_string_conversions.h"

namespace content {

constexpr char kAllowOptEmptyStr[] = "@ALLOW-EMPTY:";
constexpr char kAllowOptStr[] = "@ALLOW:";
constexpr char kDenyOptStr[] = "@DENY:";

AXTreeServer::AXTreeServer(base::ProcessId pid,
                           base::string16& filters_path,
                           bool use_json) {
  std::unique_ptr<AccessibilityTreeFormatter> formatter(
      AccessibilityTreeFormatter::Create());

  // Get accessibility tree as nested dictionary.
  base::string16 accessibility_contents_utf16;
  std::unique_ptr<base::DictionaryValue> dict =
      formatter->BuildAccessibilityTreeForProcess(pid);

  if (!dict) {
    std::cout << "Error: Failed to get accessibility tree";
    return;
  }

  Format(*formatter, *dict, filters_path, use_json);
}

AXTreeServer::AXTreeServer(gfx::AcceleratedWidget widget,
                           base::string16& filters_path,
                           bool use_json) {
  std::unique_ptr<AccessibilityTreeFormatter> formatter(
      AccessibilityTreeFormatter::Create());

  // Get accessibility tree as nested dictionary.
  std::unique_ptr<base::DictionaryValue> dict =
      formatter->BuildAccessibilityTreeForWindow(widget);

  if (!dict) {
    std::cout << "Failed to get accessibility tree";
    return;
  }

  Format(*formatter, *dict, filters_path, use_json);
}

std::vector<AccessibilityTreeFormatter::Filter> GetFilters(
    const base::string16& filters_path) {
  std::vector<AccessibilityTreeFormatter::Filter> filters;
  if (!filters_path.empty()) {
    std::string raw_filters_text;
    base::ScopedAllowBlockingForTesting allow_io_for_test_setup;
    if (base::ReadFileToString(base::FilePath(filters_path),
                               &raw_filters_text)) {
      for (const std::string& line :
           base::SplitString(raw_filters_text, "\n", base::TRIM_WHITESPACE,
                             base::SPLIT_WANT_ALL)) {
        if (base::StartsWith(line, kAllowOptEmptyStr,
                             base::CompareCase::SENSITIVE)) {
          filters.push_back(AccessibilityTreeFormatter::Filter(
              base::UTF8ToUTF16(line.substr(strlen(kAllowOptEmptyStr))),
              AccessibilityTreeFormatter::Filter::ALLOW_EMPTY));
        } else if (base::StartsWith(line, kAllowOptStr,
                                    base::CompareCase::SENSITIVE)) {
          filters.push_back(AccessibilityTreeFormatter::Filter(
              base::UTF8ToUTF16(line.substr(strlen(kAllowOptStr))),
              AccessibilityTreeFormatter::Filter::ALLOW));
        } else if (base::StartsWith(line, kDenyOptStr,
                                    base::CompareCase::SENSITIVE)) {
          filters.push_back(AccessibilityTreeFormatter::Filter(
              base::UTF8ToUTF16(line.substr(strlen(kDenyOptStr))),
              AccessibilityTreeFormatter::Filter::DENY));
        }
      }
    }
  }
  if (filters.empty()) {
    filters = {AccessibilityTreeFormatter::Filter(
        base::ASCIIToUTF16("*"), AccessibilityTreeFormatter::Filter::ALLOW)};
  }

  return filters;
}

void AXTreeServer::Format(AccessibilityTreeFormatter& formatter,
                          base::DictionaryValue& dict,
                          base::string16& filters_path,
                          bool use_json) {
  std::vector<AccessibilityTreeFormatter::Filter> filters =
      GetFilters(filters_path);

  // Set filters.
  formatter.SetFilters(filters);

  std::string accessibility_contents_utf8;

  // Format accessibility tree as JSON or text.
  if (use_json) {
    const std::unique_ptr<base::DictionaryValue> filtered_dict =
        formatter.FilterAccessibilityTree(dict);
    base::JSONWriter::WriteWithOptions(*filtered_dict,
                                       base::JSONWriter::OPTIONS_PRETTY_PRINT,
                                       &accessibility_contents_utf8);
  } else {
    base::string16 accessibility_contents_utf16;
    formatter.FormatAccessibilityTree(dict, &accessibility_contents_utf16);
    accessibility_contents_utf8 =
        base::UTF16ToUTF8(accessibility_contents_utf16);
  }

  // Write to console.
  std::cout << accessibility_contents_utf8;
}

}  // namespace content
