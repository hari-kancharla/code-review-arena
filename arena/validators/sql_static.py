"""Static checks for tenant-scoped SQL access."""

from __future__ import annotations

import re

from arena.validators.base import (
    BaseValidator,
    ValidatorContext,
    ValidatorResult,
    read_expected_source,
)

# Clauses that end a WHERE predicate. Everything between WHERE and the nearest of
# these (or the statement terminator) is the predicate the scope must live in.
_WHERE_TERMINATORS = (
    r";",
    # Shipped cases embed SQL in a host-language string, so the closing quote ends
    # the statement just as a semicolon would. Without these the predicate would
    # run on into surrounding code and a scope column mentioned anywhere below --
    # an ownership check in the function, say -- would read as a scoped query.
    r'"""',
    r"'''",
    r"\bgroup\s+by\b",
    r"\border\s+by\b",
    r"\bhaving\b",
    r"\bwindow\b",
    r"\blimit\b",
    r"\boffset\b",
    r"\bfetch\b",
    r"\bunion\b",
    r"\bexcept\b",
    r"\bintersect\b",
    r"\breturning\b",
)


def where_predicates(sql: str) -> list[str]:
    """The predicate text of each WHERE clause in a statement.

    Scoping the search to the predicate is the whole point: a tenant column named
    anywhere else -- most obviously in the SELECT list -- does not restrict which
    rows come back, so matching it there would call an unscoped query safe.

    Escape sequences are normalized first: SQL embedded in a host-language string
    may be written with \\n rather than real newlines, which would otherwise glue
    the escape letter onto the keyword ("...\\nWHERE" reads as "nWHERE") and hide
    the clause from a word-boundary match.
    """
    sql = re.sub(r"\\[nrt]", " ", sql)
    predicates: list[str] = []
    for match in re.finditer(r"\bwhere\b", sql, re.IGNORECASE):
        rest = sql[match.end() :]
        end = len(rest)
        for terminator in _WHERE_TERMINATORS:
            found = re.search(terminator, rest, re.IGNORECASE)
            if found:
                end = min(end, found.start())
        predicates.append(rest[:end])
    return predicates


class SQLHasTenantOrOwnerFilter(BaseValidator):
    name = "sql_has_tenant_or_owner_filter"

    _SCOPES = (
        "tenant_id",
        "org_id",
        "organization_id",
        "team_id",
        "owner_id",
        "account_id",
    )

    def validate(self, context: ValidatorContext) -> ValidatorResult:
        _, text = read_expected_source(context)
        predicates = where_predicates(text)
        found = sorted(
            {
                scope
                for predicate in predicates
                for scope in self._SCOPES
                if scope in predicate.lower()
            }
        )
        # Every WHERE in the file must be scoped; one unscoped read still leaks.
        passed = bool(predicates) and all(
            any(scope in predicate.lower() for scope in self._SCOPES) for predicate in predicates
        )
        return ValidatorResult(
            name=self.name,
            passed=passed,
            confidence=0.98 if passed else 0.96,
            message=(
                "Document query contains an ownership or tenant predicate."
                if passed
                else "Document query filters by resource ID without ownership scope."
            ),
            evidence=[f"Scoped predicate references `{scope}`." for scope in found]
            if passed
            else [],
        )
