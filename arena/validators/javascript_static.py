"""Optional static validators for TypeScript benchmark cases.

These establish the safety property the seeded defect is about, rather than
looking for a token anywhere in the file. A bare substring check is satisfied by
a dead line that changes no behaviour, which lets a patch that leaves the defect
completely intact earn a validated repair (see docs/structural-validators.md:
"A validator should establish the required safety property, not require one mock
patch's exact spelling").
"""

from __future__ import annotations

import re

from arena.validators.base import (
    BaseValidator,
    ValidatorContext,
    ValidatorResult,
    read_expected_source,
)

_ARROW_OR_FUNCTION = re.compile(r"^\s*(\(?\s*[\w{}[\],\s:]*\)?\s*=>|function\b)")


def call_arguments(text: str, callee: str) -> list[str]:
    """Argument text of each `callee(...)` call, matched by balanced parentheses.

    Scanning to the matching close paren is what makes these validators local to
    the call in question: a token found somewhere else in the file, in an
    unrelated statement, can no longer satisfy the check.
    """
    arguments: list[str] = []
    for match in re.finditer(re.escape(callee) + r"\s*\(", text):
        opening = text.index("(", match.end() - 1)
        depth = 0
        for index in range(opening, len(text)):
            character = text[index]
            if character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
                if depth == 0:
                    arguments.append(text[opening + 1 : index])
                    break
    return arguments


class ReactUsesFunctionalStateUpdate(BaseValidator):
    name = "react_uses_functional_state_update"

    def validate(self, context: ValidatorContext) -> ValidatorResult:
        _, text = read_expected_source(context)
        # The property: the argument to the setter is an updater FUNCTION of the
        # previous state, not a value computed from the captured variable. Checking
        # the argument itself means an arrow function elsewhere in the file (even
        # one spreading a similarly named variable) cannot satisfy it.
        updates = call_arguments(text, "setMessages")
        passed = bool(updates) and all(_ARROW_OR_FUNCTION.match(argument) for argument in updates)
        return ValidatorResult(
            name=self.name,
            passed=passed,
            confidence=0.9,
            message="State update uses the prior value function."
            if passed
            else "State update still closes over stale state.",
            evidence=[f"Functional state update: setMessages({updates[0].strip()})"]
            if passed and updates
            else [],
        )


class GraphQLUsesBatchingOrDataLoader(BaseValidator):
    name = "graphql_uses_batching_or_dataloader"

    # Any of these signals a bulk load; the list stays broad so several repair
    # styles are accepted, per the validator design principle.
    _BATCHING_TOKENS = ("dataloader", "loadmany", "findbyids", "findmany", "wherein", "batch")

    def validate(self, context: ValidatorContext) -> ValidatorResult:
        _, text = read_expected_source(context)
        lower = text.lower()
        # The defect IS the awaited per-item call inside the map callback, so its
        # absence is the property to establish. A dead line mentioning "batch"
        # leaves this true and no longer passes.
        per_item_await = any(
            re.search(r"\bawait\b", argument) for argument in call_arguments(text, ".map")
        )
        batches = any(token in lower for token in self._BATCHING_TOKENS)
        passed = batches and not per_item_await
        if per_item_await:
            message = "Resolver still awaits inside a per-item map callback."
        elif not batches:
            message = "Resolver shows no batched load."
        else:
            message = "Resolver uses batching semantics."
        return ValidatorResult(
            name=self.name,
            passed=passed,
            confidence=0.85,
            message=message,
            evidence=["Batched load with no per-item await in map."] if passed else [],
        )
