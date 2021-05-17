from sqlalchemy import schema as sa_schema
from sqlalchemy.sql.schema import Column
from sqlalchemy.sql.schema import Constraint
from sqlalchemy.sql.schema import Index
from sqlalchemy.types import Integer
from sqlalchemy.types import NULLTYPE

from .. import util
from ..util import sqla_compat
from ..util.compat import raise_
from ..util.compat import string_types


class SchemaObjects(object):
    def __init__(self, migration_context=None):
        self.migration_context = migration_context

    def primary_key_constraint(self, name, table_name, cols, schema=None):
        m = self.metadata()
        columns = [sa_schema.Column(n, NULLTYPE) for n in cols]
        t = sa_schema.Table(table_name, m, *columns, schema=schema)
        p = sa_schema.PrimaryKeyConstraint(*[t.c[n] for n in cols], name=name)
        return p

    def foreign_key_constraint(
        self,
        name,
        source,
        referent,
        local_cols,
        remote_cols,
        onupdate=None,
        ondelete=None,
        deferrable=None,
        source_schema=None,
        referent_schema=None,
        initially=None,
        match=None,
        **dialect_kw
    ):
        m = self.metadata()
        if source == referent and source_schema == referent_schema:
            t1_cols = local_cols + remote_cols
        else:
            t1_cols = local_cols
            sa_schema.Table(
                referent,
                m,
                *[sa_schema.Column(n, NULLTYPE) for n in remote_cols],
                schema=referent_schema
            )

        t1 = sa_schema.Table(
            source,
            m,
            *[sa_schema.Column(n, NULLTYPE) for n in t1_cols],
            schema=source_schema
        )

        tname = (
            "%s.%s" % (referent_schema, referent)
            if referent_schema
            else referent
        )

        dialect_kw["match"] = match

        f = sa_schema.ForeignKeyConstraint(
            local_cols,
            ["%s.%s" % (tname, n) for n in remote_cols],
            name=name,
            onupdate=onupdate,
            ondelete=ondelete,
            deferrable=deferrable,
            initially=initially,
            **dialect_kw
        )
        t1.append_constraint(f)

        return f

    def unique_constraint(self, name, source, local_cols, schema=None, **kw):
        t = sa_schema.Table(
            source,
            self.metadata(),
            *[sa_schema.Column(n, NULLTYPE) for n in local_cols],
            schema=schema
        )
        kw["name"] = name
        uq = sa_schema.UniqueConstraint(*[t.c[n] for n in local_cols], **kw)
        # TODO: need event tests to ensure the event
        # is fired off here
        t.append_constraint(uq)
        return uq

    def check_constraint(self, name, source, condition, schema=None, **kw):
        t = sa_schema.Table(
            source,
            self.metadata(),
            sa_schema.Column("x", Integer),
            schema=schema,
        )
        ck = sa_schema.CheckConstraint(condition, name=name, **kw)
        t.append_constraint(ck)
        return ck

    def generic_constraint(self, name, table_name, type_, schema=None, **kw):
        t = self.table(table_name, schema=schema)
        types = {
            "foreignkey": lambda name: sa_schema.ForeignKeyConstraint(
                [], [], name=name
            ),
            "primary": sa_schema.PrimaryKeyConstraint,
            "unique": sa_schema.UniqueConstraint,
            "check": lambda name: sa_schema.CheckConstraint("", name=name),
            None: sa_schema.Constraint,
        }
        try:
            const = types[type_]
        except KeyError as ke:
            raise_(
                TypeError(
                    "'type' can be one of %s"
                    % ", ".join(sorted(repr(x) for x in types))
                ),
                from_=ke,
            )
        else:
            const = const(name=name)
            t.append_constraint(const)
            return const

    def metadata(self):
        kw = {}
        if (
            self.migration_context is not None
            and "target_metadata" in self.migration_context.opts
        ):
            mt = self.migration_context.opts["target_metadata"]
            if hasattr(mt, "naming_convention"):
                kw["naming_convention"] = mt.naming_convention
        return sa_schema.MetaData(**kw)

    def table(self, name, *columns, **kw):
        m = self.metadata()

        cols = [
            sqla_compat._copy(c) if c.table is not None else c
            for c in columns
            if isinstance(c, Column)
        ]
        t = sa_schema.Table(name, m, *cols, **kw)

        constraints = [
            sqla_compat._copy(elem, target_table=t)
            if getattr(elem, "parent", None) is not None
            else elem
            for elem in columns
            if isinstance(elem, (Constraint, Index))
        ]

        for const in constraints:
            t.append_constraint(const)

        for f in t.foreign_keys:
            self._ensure_table_for_fk(m, f)
        return t

    def column(self, name, type_, **kw):
        return sa_schema.Column(name, type_, **kw)

    def index(self, name, tablename, columns, schema=None, **kw):
        t = sa_schema.Table(
            tablename or "no_table",
            self.metadata(),
            schema=schema,
        )
        kw["_table"] = t
        idx = sa_schema.Index(
            name,
            *[util.sqla_compat._textual_index_column(t, n) for n in columns],
            **kw
        )
        return idx

    def _parse_table_key(self, table_key):
        if "." in table_key:
            tokens = table_key.split(".")
            sname = ".".join(tokens[0:-1])
            tname = tokens[-1]
        else:
            tname = table_key
            sname = None
        return (sname, tname)

    def _ensure_table_for_fk(self, metadata, fk):
        """create a placeholder Table object for the referent of a
        ForeignKey.

        """
        if isinstance(fk._colspec, string_types):
            table_key, cname = fk._colspec.rsplit(".", 1)
            sname, tname = self._parse_table_key(table_key)
            if table_key not in metadata.tables:
                rel_t = sa_schema.Table(tname, metadata, schema=sname)
            else:
                rel_t = metadata.tables[table_key]
            if cname not in rel_t.c:
                rel_t.append_column(sa_schema.Column(cname, NULLTYPE))
