# Mux chained comparisons must be rejected

## Expected language behavior

Comparison operators are non-associative in Mux. A source expression may use at
most one comparison operator:

```mux
a < b          // valid
a < b < c      // invalid
a < b > c      // invalid as a comparison chain
```

A second comparison operator must be a syntax error, not a left-associative
binary expression.

## What the tree-sitter grammar did

The grammar previously used `repeat` for comparison operators:

```js
comparison_expression: $ => prec.left(seq(
  $.sum_expression,
  repeat(seq(
    choice(choice_of(syntax.operators.comparison.map(op => op.symbol)), 'in'),
    $.sum_expression
  ))
)),
```

That accepted `a < b < c` as a chain and, more importantly, made malformed
generic-like prefixes such as `a<a<a<...` spawn many competing parse paths.
The resulting tree-sitter error recovery could consume multiple gigabytes on a
100 KB input.

The current grammar uses `optional` instead:

```js
comparison_expression: $ => prec.left(seq(
  $.sum_expression,
  optional(seq(
    choice(choice_of(syntax.operators.comparison.map(op => op.symbol)), 'in'),
    $.sum_expression
  ))
)),
```

This rejects chained comparisons and fixes the parse-memory blowup. It is a
behavior change only for input that Mux should already reject.

## Compiler-side bug remains

The compiler's precedence parser still appears to accept chained comparisons.
In `mux-compiler/src/parser/expressions.rs`, `parse_precedence_parsed` loops over
operators at the same precedence and parses the right-hand side at
`op_precedence.next_higher()` for non-assignment operators. A second comparison
operator is therefore accepted syntactically.

Observed with the pinned compiler binary:

```text
$ mux check /tmp/opencode/chained.mux
error[E0308]: Operator '<' is not supported between types bool and int
```

The input was:

```mux
func main() returns void {
  auto a = 1
  auto b = 2
  auto c = 3
  a < b < c
}
```

The diagnostic is a semantic type error, not a parse error. The compiler is
parsing `a < b < c` as `(a < b) < c`; `(a < b)` has type `bool`, so the second
comparison then fails type checking. This masks the syntax bug.

## Required coverage

Tree-sitter corpus tests should cover:

- `a < b` parses as one comparison.
- `a < b < c` does not parse as a comparison chain; it should produce `ERROR`.
- `graph.Graph<string> g` still parses as a typed declaration.
- `map<string, set<int>> values` still parses as nested generic type arguments.
- `identity<int>(1)` still parses as a generic call.

The stress check should keep covering 10,000-, 20,000-, and 50,000-prefix
malformed `a<` inputs under a 1 GB virtual-memory limit and 10-second timeout.

## Follow-up

This document only covers the issue. The compiler parser needs a separate fix
so chained comparisons produce a syntax diagnostic rather than a type error.
Do not change compiler behavior as part of the tree-sitter memory fix.
