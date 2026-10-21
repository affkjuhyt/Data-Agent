"""What a query may read, checked before the source is touched: tables of the Data Space only.

Every table the SQL reads must be a dataset of the run's Data Space on the run's source, enabled
(BR-102), not deleted, and not someone else's personal table (BR-111). A table the catalogue does
not know is refused: the source login may see more than the Data Space does, and the Data Space
is the boundary (invariant 13, permissions before generation).

Row policies (UC-11-02, BR-45) are bound to the asker's directory attributes and added to the
source AST before execution. Column and mask policies are applied to the result before it is
stored or shown to the agent; hidden columns never reach the answer path.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterable, Mapping
from hashlib import sha256

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlglot import exp, parse_one

from libs.dataagent_db.models import catalog, governance
from libs.dataagent_db.models import org as org_models
from services.worker_agent.query.guard import SqlRejected


class OutOfScope(SqlRejected):
    """The query reads a table outside the Data Space or cannot be safely policy-rewritten."""


_USER_ATTRIBUTE = re.compile(r":user\.([A-Za-z_][A-Za-z0-9_]*)")
_USER_COLUMNS = {"id", "username", "department"}


async def can_ask_data_space(
    session: AsyncSession, *, data_space_id: uuid.UUID, principal_id: uuid.UUID
) -> bool:
    """Check Data Space membership before any model prompt is built.

    Older pilot fixtures predate membership rows; the steward remains the owner and an empty
    membership table is treated as the legacy open pilot state. Once a space has memberships,
    only a live direct/group grant carrying ``ask`` can enter the semantic path.
    """
    space = await session.scalar(
        select(org_models.DataSpace).where(org_models.DataSpace.id == data_space_id)
    )
    if space is None or space.steward_id == principal_id:
        return space is not None
    member = org_models.DataSpaceMember
    all_members = await session.scalar(
        select(func.count()).select_from(member).where(member.data_space_id == data_space_id)
    )
    if not all_members:
        return True
    groups = select(org_models.UserGroupMember.group_id).where(
        org_models.UserGroupMember.user_id == principal_id
    )
    rows = (
        await session.execute(
            select(member, org_models.Role.capabilities)
            .join(org_models.Role, org_models.Role.id == member.role_id)
            .where(
                member.data_space_id == data_space_id,
                or_(
                    and_(member.principal_type == "user", member.principal_id == principal_id),
                    and_(member.principal_type == "group", member.principal_id.in_(groups)),
                ),
                or_(member.valid_until.is_(None), member.valid_until > func.now()),
            )
        )
    ).all()
    return any("ask" in (capabilities or []) for _, capabilities in rows)


# Who runs a query (applies_to.actors, the Holistics review's "the AI sees less than the
# person"): the agent when it answers, runs or replays, so its text and figures reach a model; the
# person when they read data themselves (the explorer, a tile they refresh). Only a period close,
# which recomputes filed figures from frozen definitions, is a system caller no actor condition
# limits. Anything else, a pipeline (its SQL may be the agent's) or a caller added later, is taken
# as the agent: a policy written for the agent is the stricter one, so an unknown caller fails
# closed.
AGENT_CALLERS = frozenset({"question", "agent_run", "skill_run", "eval", "receipt_replay"})
PERSON_CALLERS = frozenset({"ad_hoc", "tile"})
SYSTEM_CALLERS = frozenset({"period"})


def actor_of(caller_kind: str | None, user_kind: str | None) -> str | None:
    """ "agent", "person" or None (a vetted system caller). An agent's own account is an agent
    whatever calls."""
    if user_kind == "agent" or caller_kind in AGENT_CALLERS:
        return "agent"
    if caller_kind in PERSON_CALLERS:
        return "person"
    if caller_kind in SYSTEM_CALLERS:
        return None
    return "agent"


async def active_policies(
    session: AsyncSession,
    *,
    principal_id: uuid.UUID,
    datasets: Iterable[catalog.Dataset],
    caller_kind: str | None = None,
) -> list[governance.AccessPolicy]:
    """Return policies that apply to this principal; an empty applies_to means everyone.

    `applies_to.actors` (["agent"], ["person"]) narrows a policy to queries the agent runs, or the
    person runs themselves; empty is both. An unknown caller is taken as the agent (actor_of)."""
    found = list(datasets)
    if not found:
        return []
    user = await session.scalar(
        select(org_models.AppUser).where(org_models.AppUser.id == principal_id)
    )
    if user is None:
        raise OutOfScope("the query principal is not an active organisation user")
    role_ids = {
        str(role_id)
        for role_id in (
            await session.scalars(
                select(org_models.UserRole.role_id).where(
                    org_models.UserRole.user_id == principal_id
                )
            )
        ).all()
    }
    group_ids = {
        str(group_id)
        for group_id in (
            await session.scalars(
                select(org_models.UserGroupMember.group_id).where(
                    org_models.UserGroupMember.user_id == principal_id
                )
            )
        ).all()
    }
    policies = (
        await session.scalars(
            select(governance.AccessPolicy).where(
                governance.AccessPolicy.dataset_id.in_([dataset.id for dataset in found]),
                governance.AccessPolicy.status == "active",
            )
        )
    ).all()

    actor = actor_of(caller_kind, user.kind)

    def applies(policy: governance.AccessPolicy) -> bool:
        raw = policy.applies_to if isinstance(policy.applies_to, dict) else {}
        users = {str(value) for value in raw.get("user_ids", [])}
        roles = {str(value) for value in raw.get("role_ids", [])}
        groups = {str(value) for value in raw.get("group_ids", [])}
        actors = {str(value) for value in raw.get("actors", [])}
        if actors and actor not in actors:
            return False
        return (
            not (users or roles or groups)
            or str(principal_id) in users
            or bool(roles & role_ids)
            or bool(groups & group_ids)
        )

    return [policy for policy in policies if applies(policy)]


async def active_row_scopes(
    session: AsyncSession, *, data_space_id: uuid.UUID, principal_id: uuid.UUID
) -> list[dict[str, list[object]]] | None:
    """Return the union of live membership scopes, or ``None`` for unrestricted access.

    A scoped grant is a row-level boundary on every dataset in the Data Space. Grants for the
    same key are a union (``IN``); different keys are an intersection. A full-space grant wins,
    while a configured space with no usable ``ask`` grant is refused by the query path.
    """
    space = await session.scalar(
        select(org_models.DataSpace).where(org_models.DataSpace.id == data_space_id)
    )
    if space is None or space.steward_id == principal_id:
        return None
    member = org_models.DataSpaceMember
    all_members = await session.scalar(
        select(func.count()).select_from(member).where(member.data_space_id == data_space_id)
    )
    if not all_members:
        return None
    groups = select(org_models.UserGroupMember.group_id).where(
        org_models.UserGroupMember.user_id == principal_id
    )
    rows = (
        await session.execute(
            select(member.row_scope, org_models.Role.capabilities)
            .join(org_models.Role, org_models.Role.id == member.role_id)
            .where(
                member.data_space_id == data_space_id,
                or_(
                    and_(member.principal_type == "user", member.principal_id == principal_id),
                    and_(member.principal_type == "group", member.principal_id.in_(groups)),
                ),
                or_(member.valid_until.is_(None), member.valid_until > func.now()),
            )
        )
    ).all()
    ask_scopes = [scope for scope, capabilities in rows if "ask" in (capabilities or [])]
    if not ask_scopes:
        raise OutOfScope("no active ask membership for this Data Space")
    if any(scope is None for scope in ask_scopes):
        return None
    normalized: list[dict[str, list[object]]] = []
    for scope in ask_scopes:
        if not isinstance(scope, Mapping):
            raise OutOfScope("membership row scope must be an object")
        current: dict[str, list[object]] = {}
        for key, raw_values in scope.items():
            values = raw_values if isinstance(raw_values, list) else [raw_values]
            if not key or not values:
                raise OutOfScope("membership row scope contains an empty boundary")
            current[str(key)] = values
        normalized.append(current)
    return normalized


_USER_SLOT = "__da_user_attr_"


def _bind_user(condition: str, user: org_models.AppUser, dialect: str) -> exp.Expression:
    """The policy condition with each :user.attr as a string literal of the asker's value.

    The values go into the AST, not the SQL text: quoting them by hand (doubling ') let a
    backslash in a directory attribute break out of the literal on MySQL.
    """
    attributes = user.attributes if isinstance(user.attributes, dict) else {}
    values: list[str] = []

    def slot(match: re.Match[str]) -> str:
        name = match.group(1)
        found = getattr(user, name, None) if name in _USER_COLUMNS else attributes.get(name)
        if found is None:
            raise OutOfScope(f"missing user attribute {name} for access policy (BR-45)")
        values.append(str(found))
        return f"{_USER_SLOT}{len(values) - 1}"

    tree = parse_one(_USER_ATTRIBUTE.sub(slot, condition), dialect=dialect)

    def literal(node: exp.Expression) -> exp.Expression:
        if isinstance(node, exp.Column) and not node.table and node.name.startswith(_USER_SLOT):
            return exp.Literal.string(values[int(node.name.removeprefix(_USER_SLOT))])
        return node

    return tree.transform(literal)


def _selects(tree: exp.Expression) -> list[exp.Select]:
    """Every SELECT of the query once (find_all yields the root itself too)."""
    seen: dict[int, exp.Select] = {}
    for node in tree.find_all(exp.Select):
        seen.setdefault(id(node), node)
    return list(seen.values())


def _direct_tables(select_node: exp.Select) -> list[exp.Table]:
    """The tables a SELECT reads itself, in its FROM and JOINs (not through a subquery)."""
    from_clause = select_node.args.get("from") or select_node.args.get("from_")
    source = from_clause.args.get("this") if from_clause is not None else None
    tables = [source] if isinstance(source, exp.Table) else []
    tables.extend(
        join.this
        for join in select_node.args.get("joins") or []
        if isinstance(join.this, exp.Table)
    )
    return tables


def _and_where(select_node: exp.Select, predicate: exp.Expression) -> None:
    existing = select_node.args.get("where")
    select_node.set(
        "where",
        exp.Where(this=exp.and_(existing.this, predicate) if existing is not None else predicate),
    )


def _resolved(ident: exp.Expression | None, source: catalog.Source) -> str | None:
    """A name as the source resolves it (guard.tables_of): PostgreSQL folds an unquoted name to
    lower case and keeps a quoted one; the other engines compare without case."""
    if not isinstance(ident, exp.Identifier):
        return None
    text = str(ident.this)
    return text if source.kind == "postgresql" and ident.quoted else text.lower()


def _reads(table: exp.Table, dataset: catalog.Dataset, source: catalog.Source) -> bool:
    """Whether a table in the query is the dataset, compared as datasets_read compares: the name
    as the source resolves it and the schema an unqualified name resolves to. An exact test on
    the name as written let `FROM loan JOIN LOAN` filter one read and leave the other unfiltered,
    and missed `FROM LOAN` on PostgreSQL, which reads the table loan."""
    name = _resolved(table.this, source)
    schema = _resolved(table.args.get("db"), source) or default_schema(source)
    return (
        name is not None
        and _same(name, dataset.table_name, source)
        and (
            schema is None or not dataset.schema_name or _same(schema, dataset.schema_name, source)
        )
    )


async def covered_columns(
    session: AsyncSession, policies: Iterable[governance.AccessPolicy]
) -> dict[uuid.UUID, list[str]]:
    """The current columns, in order, of each table a column or mask policy covers (the catalog's
    column_meta of its newest dataset version); rewrite_column_policies needs every one of them."""
    ids = {p.dataset_id for p in policies if p.kind in {"column", "mask"}}
    if not ids:
        return {}
    meta = catalog.ColumnMeta
    rows = (
        await session.execute(
            select(meta.dataset_id, meta.dataset_version, meta.name)
            .where(meta.dataset_id.in_(ids), meta.removed_in_version.is_(None))
            .order_by(meta.dataset_id, meta.dataset_version.desc(), meta.ordinal)
        )
    ).all()
    newest: dict[uuid.UUID, int] = {}
    out: dict[uuid.UUID, list[str]] = {}
    for dataset_id, version, name in rows:
        if newest.setdefault(dataset_id, version) == version:
            out.setdefault(dataset_id, []).append(name)
    return out


def _masked(column: str, method: str) -> exp.Expression:
    """A masked column in SQL, as the api's simulation shows it (governance/policies._MASKS)."""
    text = exp.cast(exp.column(column, quoted=True), "TEXT")
    if method == "hash":
        return exp.MD5(this=text)
    if method == "partial":
        return exp.DPipe(
            this=exp.Left(this=text, expression=exp.Literal.number(2)),
            expression=exp.Literal.string("***"),
        )
    if method == "last4":
        return exp.DPipe(
            this=exp.Literal.string("****"),
            expression=exp.Right(this=text, expression=exp.Literal.number(4)),
        )
    if method == "null":
        return exp.Null()
    raise OutOfScope(f"unsupported mask method {method}")


# What a covered table node may carry and still be read through its masked subquery.
_PLAIN_TABLE = frozenset({"this", "db", "catalog", "alias", "only"})


def rewrite_column_policies(
    sql: str,
    *,
    dialect: str,
    policies: Iterable[governance.AccessPolicy],
    datasets: Iterable[catalog.Dataset],
    source: catalog.Source,
    columns: Mapping[uuid.UUID, list[str]],
) -> str:
    """Hide and mask columns in the SQL itself, before the source reads the table.

    Every read of a covered table becomes `(SELECT <its columns> FROM table) AS <its alias>`, a
    hidden column left out and a masked one replaced by its masking expression under its own name.
    Whatever the rest of the query does then sees the masked table only: a rename, an expression,
    a WHERE, a UNION, a whole-row reference (`row_to_json(c)`, `c.*`, PostgreSQL's `c.text`). The
    security review found each of those past a check on result column names, which is why the
    masking is no longer done on the result. A covered table without its columns in the catalog is
    refused: its columns cannot be listed, so none can be left out.
    """
    hidden: dict[uuid.UUID, set[str]] = {}
    masks: dict[uuid.UUID, dict[str, str]] = {}
    for policy in policies:
        names = {str(c).lower() for c in policy.columns or []}
        if policy.kind == "column":
            hidden.setdefault(policy.dataset_id, set()).update(names)
        elif policy.kind == "mask":
            for name in names:
                previous = masks.setdefault(policy.dataset_id, {}).get(name)
                if previous is not None and previous != policy.mask_method:
                    raise OutOfScope(f"conflicting masks for column {name}")
                masks[policy.dataset_id][name] = str(policy.mask_method)
    covered = [d for d in datasets if d.id in hidden or d.id in masks]
    if not covered:
        return sql
    tree = parse_one(sql, dialect=dialect)
    made: set[int] = set()
    for table in list(tree.find_all(exp.Table)):
        matched = [d for d in covered if _reads(table, d, source)]
        if not matched:
            continue
        # A table node can carry more than its name: a parenthesised join, a lateral, a pivot,
        # a sample. Copied into the subquery, those would bring another read of a covered table
        # along unmasked (the security review), so a covered table must be a plain name.
        extra = [k for k, v in table.args.items() if v and k not in _PLAIN_TABLE]
        if extra:
            raise OutOfScope(
                f"{table.name} is read with {', '.join(sorted(extra))}; a table with a hidden or "
                "masked column is only read by its plain name"
            )
        # Several datasets can name one table: every one's policies apply, and they must agree.
        listings = {tuple(columns.get(d.id) or ()) for d in matched}
        listed = next(iter(listings)) if len(listings) == 1 else ()
        if not listed:
            raise OutOfScope(
                f"the columns of {table.name} are not in the catalog, or its datasets list them "
                "differently; a table with a hidden or masked column is only read once they agree"
            )
        drop: set[str] = set()
        method_of: dict[str, str] = {}
        for dataset in matched:
            drop |= hidden.get(dataset.id, set())
            for name, method in masks.get(dataset.id, {}).items():
                if method_of.setdefault(name, method) != method:
                    raise OutOfScope(f"conflicting masks for column {name}")
        projection: list[exp.Expression] = []
        for name in listed:
            key = name.lower()
            if key in drop:
                continue
            chosen = method_of.get(key)
            value = _masked(name, chosen) if chosen else exp.column(name, quoted=True)
            projection.append(exp.alias_(value, name, quoted=True))
        if not projection:
            raise OutOfScope(f"every column of {table.name} is hidden")
        bare = exp.Table(
            this=table.this.copy(),
            db=table.args["db"].copy() if table.args.get("db") else None,
            catalog=table.args["catalog"].copy() if table.args.get("catalog") else None,
            only=table.args.get("only"),
        )
        made.add(id(bare))
        alias = table.args.get("alias")
        replacement = exp.Subquery(
            this=exp.select(*projection).from_(bare, copy=False),
            alias=alias.copy()
            if alias is not None
            else exp.TableAlias(this=exp.to_identifier(table.name)),
        )
        table.replace(replacement)
    # Whatever the rewrite missed is refused, not run: every read left of a covered table must be
    # one of the masked subqueries made above.
    for table in tree.find_all(exp.Table):
        if id(table) not in made and any(_reads(table, d, source) for d in covered):
            raise OutOfScope(f"a read of {table.name} could not be masked; query refused")
    return tree.sql(dialect=dialect)


def rewrite_access_policies(
    sql: str,
    *,
    dialect: str,
    policies: Iterable[governance.AccessPolicy],
    user: org_models.AppUser,
    datasets: Iterable[catalog.Dataset],
    source: catalog.Source,
) -> str:
    """Apply supported row policies to the executable AST; never run an unfiltered query."""
    policies = list(policies)
    if not policies:
        return sql
    dataset_by_id = {dataset.id: dataset for dataset in datasets}
    tree = parse_one(sql, dialect=dialect)
    for policy in policies:
        dataset = dataset_by_id.get(policy.dataset_id)
        if dataset is None:
            raise OutOfScope("access policy dataset is outside the query scope")
        if policy.kind != "row" or not policy.condition_expr:
            raise OutOfScope(
                f"access policy {policy.name} is not a supported row policy; query refused"
            )
        # Every read of the table gets the filter, in its own SELECT and against its own alias:
        # filtering only the first one left the other side of a UNION or a self-join unfiltered.
        reads = [
            (select_node, table)
            for select_node in _selects(tree)
            for table in _direct_tables(select_node)
            if _reads(table, dataset, source)
        ]
        if not reads:
            raise OutOfScope(f"access policy table {dataset.table_name} is not in the query")
        mentions = [table for table in tree.find_all(exp.Table) if _reads(table, dataset, source)]
        if len(mentions) != len(reads):
            raise OutOfScope(
                f"access policy table {dataset.table_name} is read where no filter can be "
                "placed; query refused"
            )
        condition = _bind_user(policy.condition_expr, user, dialect)
        for target, table in reads:
            bound = condition.copy()
            for column in bound.find_all(exp.Column):
                if not column.table:
                    column.set("table", exp.to_identifier(table.alias_or_name))
            _and_where(target, bound)
    return tree.sql(dialect=dialect)


def rewrite_row_scopes(
    sql: str,
    *,
    dialect: str,
    scopes: Iterable[dict[str, list[object]]] | None,
) -> str:
    """Add membership scopes to the direct source table before the query is executed."""
    scopes = list(scopes or [])
    if not scopes:
        return sql
    combined: dict[str, list[object]] = {}
    for scope in scopes:
        for key, values in scope.items():
            bucket = combined.setdefault(key, [])
            for value in values:
                if value not in bucket:
                    bucket.append(value)
    tree = parse_one(sql, dialect=dialect)
    select_nodes = _selects(tree)
    for key, values in combined.items():
        candidates: list[tuple[exp.Select, exp.Table]] = []
        for select_node in select_nodes:
            direct = _direct_tables(select_node)
            if not direct:
                continue
            if len(direct) == 1:
                candidates.append((select_node, direct[0]))
                continue
            qualified = {
                column.table
                for column in select_node.find_all(exp.Column)
                if column.name == key and column.table
            }
            matches = [table for table in direct if table.alias_or_name in qualified]
            if len(matches) != 1:
                # Skipping this SELECT while another one was filtered ran it unscoped (e.g. the
                # joined side of a UNION); refuse instead.
                raise OutOfScope(f"membership row scope column {key} is ambiguous in the query")
            candidates.append((select_node, matches[0]))
        if not candidates:
            raise OutOfScope(f"membership row scope column {key} is ambiguous in the query")
        for target, table in candidates:
            column = exp.column(key, table=table.alias_or_name)
            literals = [exp.Literal.string(str(value)) for value in values]
            _and_where(target, exp.In(this=column, expressions=literals))
    return tree.sql(dialect=dialect)


def apply_output_policies(
    columns: list[dict[str, str]],
    rows: list[list[object]],
    policies: Iterable[governance.AccessPolicy],
) -> tuple[list[dict[str, str]], list[list[object]]]:
    """Hide or mask result columns before any result consumer can see them."""
    hidden: set[str] = set()
    masks: dict[str, str] = {}
    for policy in policies:
        if policy.kind == "column":
            hidden.update(str(column).lower() for column in policy.columns or [])
        elif policy.kind == "mask":
            method = policy.mask_method
            if method not in {"hash", "partial", "last4", "null"}:
                raise OutOfScope(f"unsupported mask method for access policy {policy.name}")
            for column in policy.columns or []:
                key = str(column).lower()
                previous = masks.get(key)
                if previous is not None and previous != method:
                    raise OutOfScope(f"conflicting masks for result column {column}")
                masks[key] = method
    keep = [
        index
        for index, column in enumerate(columns)
        if str(column.get("name", "")).lower() not in hidden
    ]
    output_columns = [columns[index] for index in keep]
    output_rows: list[list[object]] = []
    for row in rows:
        output: list[object] = []
        for index in keep:
            value = row[index]
            method = masks.get(str(columns[index].get("name", "")).lower())
            if method is not None and value is not None:
                text_value = str(value)
                value = {
                    "hash": sha256(text_value.encode()).hexdigest(),
                    "partial": text_value[:2] + "***",
                    "last4": "****" + text_value[-4:],
                    "null": None,
                }[method]
            output.append(value)
        output_rows.append(output)
    return output_columns, output_rows


def default_schema(source: catalog.Source) -> str | None:
    """The one schema an unqualified name resolves to, None when names must be qualified.

    The executor makes the source agree: on PostgreSQL it sets search_path to exactly this schema
    (else the login's own default, "$user" first, would decide); on MySQL it is the database the
    connection USEs. A SQL Server login has its own default schema the worker cannot see, so
    names there are always written with their schema."""
    options = source.conn_options if isinstance(source.conn_options, dict) else {}
    configured = str(options.get("schema") or "") or None
    if source.kind == "postgresql":
        return configured or "public"
    if source.kind == "mysql":
        return configured or source.database_name
    return None


def resolve(tables: set[tuple[str | None, str]], source: catalog.Source) -> set[tuple[str, str]]:
    schema = default_schema(source)
    resolved: set[tuple[str, str]] = set()
    for table_schema, name in tables:
        if table_schema is None and schema is None:
            raise OutOfScope(f"write the schema of table {name}")
        resolved.add((table_schema or schema or "", name))
    return resolved


def _same(a: str, b: str, source: catalog.Source) -> bool:
    return a == b if source.kind == "postgresql" else a.lower() == b.lower()


async def datasets_read(
    session: AsyncSession,
    *,
    data_space_id: uuid.UUID,
    principal_id: uuid.UUID,
    source: catalog.Source,
    tables: set[tuple[str | None, str]],
) -> list[catalog.Dataset]:
    """The datasets the query reads; policy application happens after this scope check."""
    wanted = resolve(tables, source)
    if not wanted:
        return []
    ds = catalog.Dataset
    local = source.secret_ref == "internal://workspace"
    candidates = (
        await session.scalars(
            select(ds).where(
                ds.data_space_id == data_space_id,
                ds.source_id.is_(None) if local else ds.source_id == source.id,
                (ds.schema_name == "workspace") if local else (ds.origin != "upload"),
                ds.deleted_at.is_(None),
                ds.enabled.is_(True),
                or_(ds.scope != "personal", ds.created_by == principal_id),
            )
        )
    ).all()
    found: list[catalog.Dataset] = []
    for schema, name in sorted(wanted):
        match = [
            d
            for d in candidates
            if _same(d.schema_name, schema, source) and _same(d.table_name, name, source)
        ]
        if not match:
            raise OutOfScope(f"table {schema}.{name} is not a dataset of this Data Space")
        found += match
    return found
