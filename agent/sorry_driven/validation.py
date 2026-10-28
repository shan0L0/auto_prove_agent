from __future__ import annotations

import re

from ..dataset import _strip_imports_and_options
from ..llm import ProofDesignsOut, SorrySkeletonOut
from .tactics import _header


def _build_skeleton_code(statement: str, skeleton: SorrySkeletonOut) -> str:
    parts = []
    setup = _strip_imports_and_options(skeleton.setup_code).strip()
    if setup:
        parts.append(setup)
    parts.extend(_strip_imports_and_options(sublemma.lean_statement).strip() for sublemma in skeleton.sublemmas)
    parts.append(_declaration_prefix(statement) + " := " + skeleton.parent_proof_code.strip())
    return "\n\n".join(part for part in parts if part).strip() + "\n"


def _validate_sorry_skeleton_fields(skeleton: SorrySkeletonOut) -> str:
    """Validate structured split fields before Lean checking."""
    setup = _strip_imports_and_options(skeleton.setup_code)
    parent = skeleton.parent_proof_code
    setup_error = _forbidden_fragment_error("setup_code", setup, allow_sorry=False)
    if setup_error:
        return setup_error

    if re.search(r"\bsorry\b", parent):
        return "parent_proof_code must be sorry-free; only child lemmas may contain sorry."
    parent_error = _forbidden_fragment_error("parent_proof_code", parent, allow_sorry=False)
    if parent_error:
        return parent_error
    degenerate_err = _check_degenerate_parent_proof(parent)
    if degenerate_err:
        return degenerate_err
    for index, sublemma in enumerate(skeleton.sublemmas, start=1):
        child = _strip_imports_and_options(sublemma.lean_statement)
        child_error = _forbidden_fragment_error(f"child lemma {index}", child, allow_sorry=True)
        if child_error:
            return child_error
        if ":= by" not in child:
            return f"child lemma {index} must be a complete Lean declaration ending with `:= by sorry`."
    if not skeleton.parent_proof_code.strip():
        return "parent_proof_code is required for sorry-driven splits."
    if len(skeleton.sublemmas) == 0:
        return "At least one child lemma is required for a split."

    for index, sublemma in enumerate(skeleton.sublemmas, start=1):
        trivial_err = _check_trivial_child_conclusion(sublemma, index)
        if trivial_err:
            return trivial_err

    name_err = _check_parent_uses_real_child_names(skeleton)
    if name_err:
        return name_err
    return ""


def _check_degenerate_parent_proof(parent: str) -> str:
    body = re.sub(r"^\s*by\s*", "", parent.strip())
    preamble_re = re.compile(r"^\s*(classical|open\s+\w+|simp\s+only\s*\[\s*\])\s*$", re.MULTILINE)
    body = preamble_re.sub("", body).strip()
    if re.fullmatch(r"refine\s+[\?⟨⟩,\s_]+", body):
        return (
            "parent_proof_code is a degenerate stub (bare `refine ?_` or similar). "
            "It leaves open metavariables and will never close the goal. "
            "Write a real proof body or split it into more child lemmas."
        )
    if re.fullmatch(r"exact\s+\?_", body):
        return "parent_proof_code is a degenerate stub (`exact ?_`). Replace it with a proof term or split more."
    return ""


def _check_trivial_child_conclusion(sublemma: "SorrySublemmaOut", index: int) -> str:
    stmt = sublemma.lean_statement
    type_match = re.search(r":\s*(.+?)\s*:=\s*by\b", stmt, re.DOTALL)
    if not type_match:
        return ""
    conclusion = type_match.group(1).strip()
    if re.search(r",\s*True\s*$", conclusion) or conclusion == "True":
        return (
            f"child lemma {index} has a trivially true conclusion (`True`). "
            "A child lemma whose only predicate is `True` conveys nothing to the parent and will never "
            "contribute to closing the goal. State the actual mathematical property that must hold."
        )
    return ""


def _extract_child_names_from_skeleton(skeleton: SorrySkeletonOut) -> list[str]:
    names: list[str] = []
    for sub in skeleton.sublemmas:
        for match in re.finditer(r"\b(?:noncomputable\s+)?(?:def|lemma|theorem|abbrev)\s+(\w+)", sub.lean_statement):
            names.append(match.group(1))
    return names


def _check_parent_uses_real_child_names(skeleton: SorrySkeletonOut) -> str:
    parent = skeleton.parent_proof_code
    setup_names: set[str] = set()
    for match in re.finditer(r"\b(?:noncomputable\s+)?(?:def|lemma|theorem|abbrev)\s+(\w+)", skeleton.setup_code):
        setup_names.add(match.group(1))

    child_names: set[str] = set()
    for sub in skeleton.sublemmas:
        for match in re.finditer(r"\b(?:noncomputable\s+)?(?:def|lemma|theorem|abbrev)\s+(\w+)", sub.lean_statement):
            child_names.add(match.group(1))

    known_names = setup_names | child_names
    suspicious: list[str] = []
    for match in re.finditer(r"\b(h[_A-Z]\w*)\b", parent):
        name = match.group(1)
        if name not in known_names and name not in suspicious:
            suspicious.append(name)

    if suspicious and child_names:
        child_list = ", ".join(f"`{n}`" for n in sorted(child_names))
        sus_list = ", ".join(f"`{n}`" for n in suspicious)
        return (
            f"parent_proof_code references {sus_list} which are not declared in setup_code or sublemmas. "
            f"Use the actual child lemma names: {child_list}."
        )
    return ""


def _check_tactic_at_toplevel(code: str) -> str:
    tactic_keywords = ("have ", "let ", "intro ", "apply ", "exact ", "simp", "rw ", "rfl", "constructor", "refine ")
    for line in code.splitlines():
        if not line or line[0] in (" ", "\t"):
            continue
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        is_command = stripped.startswith(
            (
                "theorem ",
                "lemma ",
                "def ",
                "noncomputable ",
                "abbrev ",
                "instance ",
                "class ",
                "structure ",
                "inductive ",
                "namespace ",
                "open ",
                "variable ",
                "section ",
                "end ",
                "attribute ",
                "@[",
            )
        )
        if not is_command and any(stripped.startswith(kw) for kw in tactic_keywords):
            return (
                f"setup_code contains a tactic-mode statement at the top level (column 0): `{stripped[:80]}`. "
                "`setup_code` must contain only top-level Lean commands."
            )
        # `classical` is a tactic keyword; at the top level Lean parses it as a
        # function application and raises "Function expected".
        if re.fullmatch(r"classical\s*", stripped):
            return (
                "setup_code contains a bare `classical` token at the top level. "
                "`classical` is a tactic keyword, not a term. "
                "Use `open Classical` as a top-level declaration if you need classical logic, "
                "or put `classical` inside a `by` tactic block within a proof body."
            )
    return ""


def _check_fin_modulo_pattern(code: str, field: str) -> str:
    errors: list[str] = []
    if re.search(r"⟨[^⟩]*%\s*\w+\s*,\s*by\s+omega\s*⟩", code):
        errors.append(
            f"{field}: `by omega` used directly as the `Fin` bound proof inside `⟨k % m, by omega⟩`. "
            "Use `Nat.mod_lt _ hm0` where `hm0 : 0 < m`."
        )
    if re.search(r"\(by\s+omega\s*:\s*0\s*<\s*\w+\)", code):
        errors.append(
            f"{field}: `(by omega : 0 < m)` used as an inline proof term. Add the bound as a parameter or use `i.pos`."
        )
    for line in code.splitlines():
        if "Nat.mod_lt" in line and re.search(r"\bby\s+omega\b", line):
            errors.append(
                f"{field}: `Nat.mod_lt ... (by omega)` on line `{line.strip()[:100]}`. "
                "Use a named `hm0 : 0 < m` from a variable or parameter instead."
            )
            break
    return "\n".join(errors) if errors else ""


def _check_free_theorem_variables(code: str, field: str) -> str:
    free_suspects = ["π", "hm", "hπ_pos", "hπ_sum", "hπ"]
    issues: list[str] = []
    variable_covered: set[str] = set()
    for var_line in code.splitlines():
        stripped_var = var_line.strip()
        if stripped_var.startswith("variable"):
            for suspect in free_suspects:
                if re.search(r"\b" + re.escape(suspect) + r"\b", stripped_var):
                    variable_covered.add(suspect)

    decl_pattern = re.compile(r"^(noncomputable\s+)?(def|lemma|theorem|abbrev)\s+(\w+)(.*?):=", re.MULTILINE | re.DOTALL)
    for match in decl_pattern.finditer(code):
        param_section = match.group(4)
        body_start = match.end()
        next_decl = decl_pattern.search(code, body_start)
        body = code[body_start : next_decl.start() if next_decl else len(code)]
        for suspect in free_suspects:
            if suspect in variable_covered:
                continue
            suspect_re = re.compile(r"\b" + re.escape(suspect) + r"\b")
            if suspect_re.search(body) and not suspect_re.search(param_section):
                issues.append(
                    f"{field}: `{match.group(3)}` uses `{suspect}` in its body but does not declare it as a parameter."
                )
    return "\n".join(issues) if issues else ""


def _lean_pattern_hints(code_or_error: str) -> list[str]:
    hints: list[str] = []
    for checker in [
        _check_tactic_at_toplevel,
        lambda code: _check_fin_modulo_pattern(code, field="Lean code"),
        lambda code: _check_free_theorem_variables(code, field="Lean code"),
    ]:
        hint = checker(code_or_error)
        if hint and hint not in hints:
            hints.append(hint)
    return hints


def _parent_proof_sorry_error(skeleton: SorrySkeletonOut) -> str:
    if skeleton.parent_proof_code.strip():
        if "sorry" in skeleton.parent_proof_code:
            return "parent_proof_code must be sorry-free; only child lemmas may contain sorry."
        return ""
    return ""


def _forbidden_fragment_error(field_name: str, code: str, allow_sorry: bool) -> str:
    checks = [
        ("admit", r"\badmit\b"),
        ("axiom", r"\baxiom\b"),
        ("native_decide", r"\bnative_decide\b"),
        ("placeholder `...`", r"\.\.\."),
        ("import", r"^\s*import\s+"),
        ("set_option", r"^\s*set_option\s+"),
    ]
    if not allow_sorry:
        checks.insert(0, ("sorry", r"\bsorry\b"))
    for label, pattern in checks:
        if re.search(pattern, code, flags=re.MULTILINE):
            return f"{field_name} contains forbidden token: {label}."
    return ""


def _phase_error(phase: str, output: str) -> str:
    return f"Skeleton validation failed in {phase}.\n\n{output}"


def _apply_design_review(designs: "ProofDesignsOut", review: "ProofDesignReviewOut") -> "ProofDesignsOut":
    """Return a copy of designs with the reviewer's selection applied."""
    if review.selected_design and any(d.name == review.selected_design for d in designs.designs):
        return designs.__class__(
            designs=designs.designs,
            selected_design=review.selected_design,
            rationale=f"[Reviewer] {review.critique}" if review.critique else designs.rationale,
        )
    return designs


def _selected_design_text(designs: ProofDesignsOut) -> str:
    selected = next((design for design in designs.designs if design.name == designs.selected_design), None)
    if selected is None and designs.designs:
        selected = max(designs.designs, key=lambda design: design.score)
    if selected is None:
        return designs.rationale
    obligations = "\n".join(f"- {item}" for item in selected.child_obligations)
    risks = "\n".join(f"- {item}" for item in selected.risks) or "- none"
    return (
        f"Selected proof design: {selected.name}\n"
        f"Construction: {selected.construction}\n"
        f"Why Lean-friendly: {selected.why_lean_friendly}\n"
        f"Expected child obligations:\n{obligations}\n"
        f"Known risks:\n{risks}\n"
        f"Selection rationale: {designs.rationale}"
    )


def _proof_design_markdown(designs: ProofDesignsOut) -> str:
    lines = ["# Proof Design", "", f"- Selected: `{designs.selected_design}`", f"- Rationale: {designs.rationale}", ""]
    for design in designs.designs:
        lines.extend(
            [
                f"## {design.name}",
                "",
                f"- Score: `{design.score:.2f}`",
                f"- Construction: {design.construction}",
                f"- Why Lean-friendly: {design.why_lean_friendly}",
                "",
                "### Child Obligations",
                "",
            ]
        )
        lines.extend(f"- {item}" for item in design.child_obligations)
        lines.extend(["", "### Risks", ""])
        lines.extend(f"- {item}" for item in (design.risks or ["none"]))
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _declaration_prefix(statement: str) -> str:
    if ":= by" in statement:
        return statement.split(":= by", 1)[0].rstrip()
    return statement.rstrip()


def _child_part_file(setup_code: str, lean_statement: str) -> str:
    parts = [_header().rstrip()]
    if setup_code:
        parts.append(setup_code)
    parts.append(_strip_imports_and_options(lean_statement).strip())
    return "\n\n".join(part for part in parts if part).rstrip() + "\n"


def _lean_safe_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", name.strip())
    cleaned = cleaned.strip("_")
    return cleaned[:80]


def _enrich_split_error(error: str) -> str:
    hints = _lean_pattern_hints(error)
    if "omega could not prove" in error and ("%" in error or "mod" in error.lower()):
        hints.append(
            "Fin modulo hint: `omega` alone cannot prove `k % m < m`. First derive `hm0 : 0 < m`, "
            "then use `Nat.mod_lt k hm0`."
        )
    if "parent_proof_code must be sorry-free" in error:
        hints.append(
            "parent_proof_code must not contain `sorry`. Move every hard sub-goal into a child lemma with `:= by sorry`."
        )
    if "type mismatch" in error.lower() or "application type mismatch" in error.lower():
        hints.append("Type mismatch in parent assembly: child lemma signatures must exactly match parent proof usage.")
    if "unsolved goals" in error.lower() and "parent assembly" in error.lower():
        hints.append("Unsolved goals in parent assembly: add or strengthen child lemmas so the parent closes without `sorry`.")
    if "Unknown constant" in error or "unknown identifier" in error.lower() or "unknownidentifier" in error.lower():
        hints.append(
            "Unknown identifier: use exact declared child lemma names in parent_proof_code, and pass theorem-local variables "
            "as explicit parameters to top-level defs or lemmas."
        )
        from ..retrieval import extract_unknown_identifiers, suggest_mathlib_alternatives
        unknown = extract_unknown_identifiers(error)
        if unknown:
            suggestion = suggest_mathlib_alternatives(unknown)
            if suggestion:
                hints.append(suggestion)
    if "no usable constraints" in error.lower() and "omega" in error.lower():
        hints.append(
            "`omega` reported 'No usable constraints'. Add a bound hypothesis such as `(hm : 3 ≤ m)` to the enclosing "
            "def/lemma, or use `i.pos` when constructing `Fin m` values."
        )
    if "failed to prove positivity" in error.lower() or (
        "failed to prove" in error.lower() and "nonnegativity" in error.lower()
    ):
        hints.append(
            "`positivity` cannot handle `min`/`max` expressions. "
            "To prove `c ≤ min a b` use `le_min h1 h2` (not `min_nonneg` — that lemma does not exist). "
            "For products/quotients of nonneg terms use `mul_nonneg` and `div_nonneg`."
        )
    if not hints:
        return error
    return error + "\n\n--- Fix hints ---\n" + "\n\n".join(hints)
