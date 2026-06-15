<!--
## Sync Impact Report — Constitution v1.0.0

**Version change**: unfilled template → 1.0.0

**Initial ratification — all principles are new.**

### Principles Added
I.   Simplicity First
II.  Think Before Coding
III. Surgical Changes
IV.  Goal-Driven Execution
V.   Reuse Over Rebuild
VI.  Async-First
VII. Security by Default
VIII. Ruff Compliance

### Sections Added
- Technology Stack
- Development Workflow

### Templates Reviewed
- ✅ plan-template.md — Constitution Check already references this file; no update needed
- ✅ spec-template.md — Generic structure compatible with all 8 principles; no update needed
- ✅ tasks-template.md — Phase/dependency model aligns with Principles I–IV; no update needed
- ✅ checklist-template.md — Generic; no principle-specific changes required
- ✅ agent-context command — No outdated references

### Deferred Items
None. All fields resolved.
-->

# mbe-api Constitution

## Core Principles

### I. Simplicity First

Minimum code that solves the stated problem — nothing speculative.

- MUST NOT add features beyond what was explicitly requested.
- MUST NOT introduce abstractions for single-use code.
- MUST NOT add configurability or flexibility that wasn't requested.
- MUST NOT add error handling for impossible or hypothetical scenarios.
- If a working solution can be achieved in 50 lines, a 200-line solution requires justification.

*Rationale*: Complexity accumulates into maintenance burden. Simplicity keeps the codebase
auditable and makes bugs easier to find.

### II. Think Before Coding

Surface assumptions and tradeoffs before implementing.

- MUST state assumptions explicitly before writing code. If uncertain, ask first.
- MUST present multiple interpretations when they exist — do not pick silently.
- MUST surface tradeoffs when a simpler approach exists.
- MUST stop and name what is confusing rather than guessing.

*Rationale*: Design mistakes are cheaper to fix than code review discoveries. Confusion
silently encoded in code becomes undefined behavior.

### III. Surgical Changes

Touch only what the task requires.

- MUST NOT improve adjacent code, comments, or formatting unless directly related to the task.
- MUST NOT refactor code that isn't broken.
- MUST match the existing style of the file being edited, even when disagreeing with it.
- MUST remove imports, variables, and functions that YOUR changes made unused.
- MUST NOT remove pre-existing dead code unless explicitly asked.
- Every changed line MUST trace directly to the user's request.

*Rationale*: Unrelated changes pollute diffs, complicate review, and introduce regressions.
Surgical changes make the source of every modification auditable.

### IV. Goal-Driven Execution

Transform every task into a verifiable goal before starting.

- MUST define success criteria before implementing multi-step tasks.
- MUST state a brief numbered plan (step → verify) for tasks with more than one step.
- MUST NOT report a task complete without having verified the success criterion.

*Examples*:
- "Add validation" → "Write tests for invalid inputs; make them pass"
- "Fix the bug" → "Write a test that reproduces it; make it pass"

*Rationale*: Weak criteria ("make it work") require constant clarification. Strong criteria
enable independent forward progress.

### V. Reuse Over Rebuild

Existing schemas, services, dependencies, and models MUST be preferred over new ones.

- Before creating a new abstraction, MUST verify no existing one satisfies the need.
- New models, schemas, or services MUST be justified in the plan's Complexity Tracking table.
- New dependencies MUST be declared in the feature spec and reviewed before introduction.

*Rationale*: Every new abstraction is future maintenance. The existing codebase has already
paid the cost of design; reuse it before paying again.

### VI. Async-First

All database operations MUST be asynchronous.

- All route handlers MUST be `async def`.
- All database access MUST use `AsyncSession` from SQLAlchemy async.
- MUST NOT call synchronous DB methods (without `await`) in an async context.
- MUST NOT block the event loop with synchronous I/O.

*Rationale*: The project uses aiomysql and FastAPI's ASGI model. Blocking calls inside async
handlers stall the entire event loop under concurrent load.

### VII. Security by Default

All non-public endpoints MUST be authenticated.

- MUST gate every endpoint with `get_current_user` unless explicitly designated public.
- MUST enforce JWT `session_version` invalidation on all sensitive mutations (password changes,
  privilege updates, user profile edits).
- MUST NOT expose user data to unauthenticated callers.
- Public endpoints (e.g., `/login`, `/recover`) MUST be explicitly marked as such in the spec.

*Rationale*: The system handles POS and financial data. Authentication failures have direct
business and compliance consequences.

### VIII. Ruff Compliance

All Python code MUST pass ruff linting before commit.

- Line length: 100 characters maximum (E501). Long `mapped_column` calls MUST use trailing-comma
  multi-line style.
- MUST NOT introduce unused imports (F401).
- Import blocks MUST be sorted per ruff I001 rules.
- Rule set: E, F, I, UP.
- Verify with: `uv run ruff check app/ migrations/ tests/`

*Rationale*: Consistent style lowers cognitive load during review. Automated enforcement
removes manual style discussions from the critical path.

## Technology Stack

Fixed constraints — MUST NOT change without a constitution amendment.

| Concern | Choice |
|---------|--------|
| Language | Python 3.12+ |
| Web framework | FastAPI (ASGI) |
| ORM | SQLAlchemy 2.0 async (`Mapped`, `mapped_column`) |
| Schema validation | Pydantic v2 |
| Auth tokens | python-jose (JWT) |
| Database driver | aiomysql (MariaDB) |
| Password hashing | SHA1, case-insensitive hex comparison (legacy system) |
| API versioning | All routes under `/api/v1/` |

## Development Workflow

### Testing

- Test files live in `tests/api/` following the existing pattern.
- Stack: pytest + pytest-asyncio + httpx `ASGITransport`.
- Tests are OPTIONAL unless explicitly requested in the feature spec.
- When tests are included: MUST write tests first, confirm they fail, then implement.

### Changelog

- MUST maintain `CHANGELOG.md` in Keep a Changelog format.
- Every feature or fix MUST update the `[Unreleased]` section before or at merge.
- Entries are grouped: Added / Changed / Removed / Fixed / Docs.

### Linting Gate

- `uv run ruff check app/ migrations/ tests/` MUST pass (zero violations) before any commit.

## Governance

This constitution supersedes all other coding practices for `mbe-api`.

**Amendment procedure**:
1. Propose the amendment with a rationale and impact assessment.
2. Record the change in `CHANGELOG.md` under a new version entry.
3. Update all dependent templates and the Sync Impact Report header in this file.
4. Bump the version per semantic rules:
   - MAJOR: backward-incompatible principle removals or redefinitions.
   - MINOR: new principle or section added or materially expanded.
   - PATCH: clarifications, wording, or typo fixes.

**Compliance**: Every plan's Constitution Check section MUST explicitly verify each principle.
Features that violate any principle MUST document the violation in the Complexity Tracking table
and receive explicit justification before implementation proceeds.

**Runtime guidance**: See `CLAUDE.md` for session-level behavioral guidelines that complement
this constitution.

**Version**: 1.0.0 | **Ratified**: 2026-06-14 | **Last Amended**: 2026-06-14
