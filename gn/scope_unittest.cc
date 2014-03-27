// Copyright (c) 2013 The Chromium Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#include "testing/gtest/include/gtest/gtest.h"
#include "tools/gn/input_file.h"
#include "tools/gn/parse_tree.h"
#include "tools/gn/scope.h"
#include "tools/gn/test_with_scope.h"

namespace {

bool HasStringValueEqualTo(const Scope* scope,
                           const char* name,
                           const char* expected_value) {
  const Value* value = scope->GetValue(name);
  if (!value)
    return false;
  if (value->type() != Value::STRING)
    return false;
  return value->string_value() == expected_value;
}

}  // namespace

TEST(Scope, NonRecursiveMergeTo) {
  TestWithScope setup;

  // Make a pretend parse node with proper tracking that we can blame for the
  // given value.
  InputFile input_file(SourceFile("//foo"));
  Token assignment_token(Location(&input_file, 1, 1), Token::STRING,
      "\"hello\"");
  LiteralNode assignment;
  assignment.set_value(assignment_token);

  Value old_value(&assignment, "hello");
  setup.scope()->SetValue("v", old_value, &assignment);

  // Detect collisions of values' values.
  {
    Scope new_scope(setup.settings());
    Value new_value(&assignment, "goodbye");
    new_scope.SetValue("v", new_value, &assignment);

    Err err;
    EXPECT_FALSE(setup.scope()->NonRecursiveMergeTo(
        &new_scope, false, &assignment, "error", &err));
    EXPECT_TRUE(err.has_error());
  }

  // The clobber flag should just overwrite colliding values.
  {
    Scope new_scope(setup.settings());
    Value new_value(&assignment, "goodbye");
    new_scope.SetValue("v", new_value, &assignment);

    Err err;
    EXPECT_TRUE(setup.scope()->NonRecursiveMergeTo(
        &new_scope, true, &assignment, "error", &err));
    EXPECT_FALSE(err.has_error());

    const Value* found_value = new_scope.GetValue("v");
    ASSERT_TRUE(found_value);
    EXPECT_TRUE(old_value == *found_value);
  }

  // Don't flag values that technically collide but have the same value.
  {
    Scope new_scope(setup.settings());
    Value new_value(&assignment, "hello");
    new_scope.SetValue("v", new_value, &assignment);

    Err err;
    EXPECT_TRUE(setup.scope()->NonRecursiveMergeTo(
        &new_scope, false, &assignment, "error", &err));
    EXPECT_FALSE(err.has_error());
  }
}

TEST(Scope, MakeClosure) {
  // Create 3 nested scopes [const root from setup] <- nested1 <- nested2.
  TestWithScope setup;

  // Make a pretend parse node with proper tracking that we can blame for the
  // given value.
  InputFile input_file(SourceFile("//foo"));
  Token assignment_token(Location(&input_file, 1, 1), Token::STRING,
      "\"hello\"");
  LiteralNode assignment;
  assignment.set_value(assignment_token);
  setup.scope()->SetValue("on_root", Value(&assignment, "on_root"),
                           &assignment);

  // Root scope should be const from the nested caller's perspective.
  Scope nested1(static_cast<const Scope*>(setup.scope()));
  nested1.SetValue("on_one", Value(&assignment, "on_one"), &assignment);

  Scope nested2(&nested1);
  nested2.SetValue("on_one", Value(&assignment, "on_two"), &assignment);
  nested2.SetValue("on_two", Value(&assignment, "on_two2"), &assignment);

  // Making a closure from the root scope.
  scoped_ptr<Scope> result = setup.scope()->MakeClosure();
  EXPECT_FALSE(result->containing());  // Should have no containing scope.
  EXPECT_TRUE(result->GetValue("on_root"));  // Value should be copied.

  // Making a closure from the second nested scope.
  result = nested2.MakeClosure();
  EXPECT_EQ(setup.scope(),
            result->containing());  // Containing scope should be the root.
  EXPECT_TRUE(HasStringValueEqualTo(result.get(), "on_root", "on_root"));
  EXPECT_TRUE(HasStringValueEqualTo(result.get(), "on_one", "on_two"));
  EXPECT_TRUE(HasStringValueEqualTo(result.get(), "on_two", "on_two2"));
}

TEST(Scope, GetMutableValue) {
  TestWithScope setup;

  // Make a pretend parse node with proper tracking that we can blame for the
  // given value.
  InputFile input_file(SourceFile("//foo"));
  Token assignment_token(Location(&input_file, 1, 1), Token::STRING,
      "\"hello\"");
  LiteralNode assignment;
  assignment.set_value(assignment_token);

  const char kOnConst[] = "on_const";
  const char kOnMutable1[] = "on_mutable1";
  const char kOnMutable2[] = "on_mutable2";

  Value value(&assignment, "hello");

  // Create a root scope with one value.
  Scope root_scope(setup.settings());
  root_scope.SetValue(kOnConst, value, &assignment);

  // Create a first nested scope with a different value.
  const Scope* const_root_scope = &root_scope;
  Scope mutable_scope1(const_root_scope);
  mutable_scope1.SetValue(kOnMutable1, value, &assignment);

  // Create a second nested scope with a different value.
  Scope mutable_scope2(&mutable_scope1);
  mutable_scope2.SetValue(kOnMutable2, value, &assignment);

  // Check getting root scope values.
  EXPECT_TRUE(mutable_scope2.GetValue(kOnConst, true));
  EXPECT_FALSE(mutable_scope2.GetMutableValue(kOnConst, true));

  // Test reading a value from scope 1.
  Value* mutable1_result = mutable_scope2.GetMutableValue(kOnMutable1, false);
  ASSERT_TRUE(mutable1_result);
  EXPECT_TRUE(*mutable1_result == value);

  // Make sure CheckForUnusedVars works on scope1 (we didn't mark the value as
  // used in the previous step).
  Err err;
  EXPECT_FALSE(mutable_scope1.CheckForUnusedVars(&err));
  mutable1_result = mutable_scope2.GetMutableValue(kOnMutable1, true);
  EXPECT_TRUE(mutable1_result);
  err = Err();
  EXPECT_TRUE(mutable_scope1.CheckForUnusedVars(&err));

  // Test reading a value from scope 2.
  Value* mutable2_result = mutable_scope2.GetMutableValue(kOnMutable2, true);
  ASSERT_TRUE(mutable2_result);
  EXPECT_TRUE(*mutable2_result == value);
}
