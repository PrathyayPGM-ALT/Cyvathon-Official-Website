"""A tiny in-memory stand-in for the Supabase client.

Supports only the query shapes main.py actually uses:
  .table(t).select(cols).eq(c,v).neq/.gte/.not_.is_(...).order(...).limit(n).execute().data
  .table(t).insert(row|rows).execute().data
  .table(t).update(patch).eq(c,v).execute().data
  .table(t).delete().eq(c,v).execute().data
"""
import itertools


class _Result:
    def __init__(self, data): self.data = data


class _Query:
    def __init__(self, db, table, kind, payload=None):
        self.db, self.table_name, self.kind = db, table, kind
        self.payload = payload
        self.filters = []          # (op, col, val)
        self._order = None
        self._desc = False
        self._limit = None
        self._negate_next = False

    # ---- filters ----
    def eq(self, c, v):  self.filters.append(("eq", c, v));  return self
    def neq(self, c, v): self.filters.append(("neq", c, v)); return self
    def gte(self, c, v): self.filters.append(("gte", c, v)); return self
    def lte(self, c, v): self.filters.append(("lte", c, v)); return self
    def lt(self, c, v):  self.filters.append(("lt", c, v));  return self
    def gt(self, c, v):  self.filters.append(("gt", c, v));  return self
    def in_(self, c, vs): self.filters.append(("in", c, list(vs))); return self
    def limit(self, n):  self._limit = n; return self
    def order(self, col, desc=False): self._order, self._desc = col, desc; return self

    @property
    def not_(self):
        self._negate_next = True
        return self

    def is_(self, c, v):
        op = "isnot" if self._negate_next else "is"
        self._negate_next = False
        self.filters.append((op, c, v))
        return self

    # ---- run ----
    def _match(self, row):
        for op, c, v in self.filters:
            got = row.get(c)
            if op == "eq" and got != v: return False
            if op == "neq" and got == v: return False
            if op == "gte" and not (got is not None and got >= v): return False
            if op == "lte" and not (got is not None and got <= v): return False
            if op == "gt" and not (got is not None and got > v): return False
            if op == "lt" and not (got is not None and got < v): return False
            if op == "in" and got not in v: return False
            if op == "is" and v == "null" and got is not None: return False
            if op == "isnot" and v == "null" and got is None: return False
        return True

    def execute(self):
        rows = self.db.data.setdefault(self.table_name, [])
        if self.kind == "select":
            out = [dict(r) for r in rows if self._match(r)]
            if self._order:
                out.sort(key=lambda r: (r.get(self._order) is None, r.get(self._order)),
                         reverse=self._desc)
            if self._limit:
                out = out[:self._limit]
            return _Result(out)

        if self.kind == "insert":
            payload = self.payload if isinstance(self.payload, list) else [self.payload]
            made = []
            for p in payload:
                row = dict(p)
                for k, v in self.db.defaults.get(self.table_name, {}).items():
                    row.setdefault(k, v)
                row.setdefault("id", next(self.db.ids))
                row.setdefault("created_at", self.db.now)
                for uq in self.db.unique.get(self.table_name, []):
                    if any(all(r.get(k) == row.get(k) for k in uq) for r in rows):
                        raise Exception("duplicate key value violates unique constraint")
                rows.append(row)
                made.append(dict(row))
            return _Result(made)

        if self.kind == "update":
            hit = []
            for r in rows:
                if self._match(r):
                    r.update(self.payload)
                    hit.append(dict(r))
            return _Result(hit)

        if self.kind == "delete":
            keep, gone = [], []
            for r in rows:
                (gone if self._match(r) else keep).append(r)
            self.db.data[self.table_name] = keep
            return _Result([dict(r) for r in gone])

        raise AssertionError(self.kind)


class _Table:
    def __init__(self, db, name): self.db, self.name = db, name
    def select(self, *a, **k): return _Query(self.db, self.name, "select")
    def insert(self, payload):  return _Query(self.db, self.name, "insert", payload)
    def update(self, payload):  return _Query(self.db, self.name, "update", payload)
    def delete(self):           return _Query(self.db, self.name, "delete")


class FakeSupabase:
    def __init__(self, now="2026-08-27T12:00:00+00:00"):
        self.data = {}
        self.ids = itertools.count(1000)
        self.now = now
        # (table -> list of unique column-tuples)
        self.unique = {"ministry_applications": [("ministry_id", "username")]}
        # Postgres column defaults the app relies on.
        self.defaults = {
            "ministry_applications": {"status": "pending", "statement": ""},
            "criminal_records": {"kind": "fine", "fine": 0, "jail_days": 0,
                                 "spent": False, "reason": ""},
            "polls":   {"open": True, "options": []},
            "cybucks": {"banned": False, "approved": True, "balance": 0},
            "loans":   {"repaid": False, "defaulted": False},
            "court_cases": {"status": "open", "fine": 0, "jail_days": 0},
            "notifications": {"read": False},
        }

    def table(self, name): return _Table(self, name)

    # convenience
    def seed(self, table, rows):
        self.data.setdefault(table, []).extend(dict(r) for r in rows)
