-- ============================================================
--  Cyvathon — Crystallines replace Pufferbucks and Aquilines
--  Because of the war, Pufferbucks and Aquilines are withdrawn.
--  Cyvathon now has exactly three currencies: Cybucks, Cybits and
--  Crystallines (from our ally Crystonia, 1 Crystalline = 1 Cybuck).
--
--  What this does:
--   1. Adds a `crystallines` column to citizens, the Treasury and companies.
--   2. Turns every Pufferbuck and Aquiline into Crystallines at full value:
--        1 Pufferbuck = 1 Crystalline, 10 Aquilines = 1 Crystalline.
--      Citizens, companies and the Treasury itself all convert.
--   3. Gives every citizen who isn't banned 100 Crystallines, paid by
--      the Treasury and logged in its ledger.
--   4. Re-prices market listings and Cyvapay links that asked for
--      Pufferbucks or Aquilines, at the same values.
--   5. Records it on every citizen's record and sends a notification.
--
--  Every conversion is written to `currency_conversion`, so you can see
--  exactly what each citizen had and got.
--  Safe & idempotent: a citizen already converted is never converted
--  (or granted) twice. Paste into the Supabase SQL editor and run.
-- ============================================================

alter table cybucks   add column if not exists crystallines numeric default 0;
alter table treasury  add column if not exists crystallines numeric default 0;
alter table companies add column if not exists crystallines numeric default 0;

-- Older databases may not have these; the conversion below reads them.
alter table cybucks   add column if not exists pufb      numeric default 0;
alter table cybucks   add column if not exists aquilines numeric default 0;
alter table treasury  add column if not exists pufb      numeric default 0;
alter table treasury  add column if not exists aquilines numeric default 0;
alter table companies add column if not exists pufb      numeric default 0;
alter table companies add column if not exists aquilines numeric default 0;
alter table cybucks   add column if not exists banned    boolean default false;

create table if not exists currency_conversion (
    holder       text primary key,      -- citizen username, 'company:<id>' or 'treasury'
    old_pufb     numeric not null default 0,
    old_aquilines numeric not null default 0,
    crystallines numeric not null default 0,   -- converted value
    grant_given  numeric not null default 0,   -- the 100 from the Treasury
    converted_at timestamptz default now()
);

do $$
declare
    r        record;
    conv     numeric;
    granted  numeric;
    total_grant numeric := 0;
begin
    -- 1. Citizens
    for r in select username, coalesce(pufb, 0) as pf, coalesce(aquilines, 0) as aq,
                    coalesce(banned, false) as banned
             from cybucks
             where username not in (select holder from currency_conversion)
    loop
        conv    := round(r.pf + r.aq / 10.0, 2);
        granted := case when r.banned then 0 else 100 end;
        update cybucks
           set crystallines = coalesce(crystallines, 0) + conv + granted,
               pufb = 0, aquilines = 0
         where username = r.username;
        insert into currency_conversion (holder, old_pufb, old_aquilines, crystallines, grant_given)
            values (r.username, r.pf, r.aq, conv, granted);
        total_grant := total_grant + granted;
        insert into records (username, entry) values (r.username,
            'Pufferbucks and Aquilines withdrawn: ' || r.pf || ' PUFB and ' || r.aq
            || ' AQ became ' || conv || ' Crystallines'
            || case when granted > 0 then ', plus a 100 Crystalline grant from the Treasury.' else '.' end);
        if not r.banned then
            insert into notifications (username, message, link) values (r.username,
                '💎 Pufferbucks and Aquilines are gone. Yours became ' || conv
                || ' Crystallines, and the Treasury gave you 100 more. Cyvathon now uses Cybucks, Cybits and Crystallines.',
                '/bank');
        end if;
    end loop;

    -- 2. Companies (no grant — just their old money, at full value)
    for r in select id, coalesce(pufb, 0) as pf, coalesce(aquilines, 0) as aq
             from companies
             where 'company:' || id not in (select holder from currency_conversion)
    loop
        conv := round(r.pf + r.aq / 10.0, 2);
        update companies
           set crystallines = coalesce(crystallines, 0) + conv, pufb = 0, aquilines = 0
         where id = r.id;
        insert into currency_conversion (holder, old_pufb, old_aquilines, crystallines)
            values ('company:' || r.id, r.pf, r.aq, conv);
    end loop;

    -- 3. The Treasury converts its own reserves, then pays the grants.
    if not exists (select 1 from currency_conversion where holder = 'treasury') then
        select coalesce(pufb, 0) as pf, coalesce(aquilines, 0) as aq into r from treasury where id = 1;
        conv := round(coalesce(r.pf, 0) + coalesce(r.aq, 0) / 10.0, 2);
        update treasury
           set crystallines = coalesce(crystallines, 0) + conv, pufb = 0, aquilines = 0
         where id = 1;
        insert into currency_conversion (holder, old_pufb, old_aquilines, crystallines)
            values ('treasury', coalesce(r.pf, 0), coalesce(r.aq, 0), conv);
    end if;
    if total_grant > 0 then
        update treasury set crystallines = coalesce(crystallines, 0) - total_grant where id = 1;
        insert into treasury_flows (direction, counterparty, currency, amount, kind)
            values ('OUT', 'All citizens', 'crys', total_grant, 'grant');
    end if;
end $$;

-- 4. Listings and payment links priced in the old money, at the same value.
do $$
begin
    update market_items set currency = 'crys'
     where currency = 'pufb';
    update market_items set currency = 'crys', price = round(price / 10.0, 2)
     where currency = 'aquilines';

    if to_regclass('public.cyvapay_links') is not null then     -- Cyvapay may not be migrated yet
        update cyvapay_links set currency = 'crys'
         where currency = 'pufb';
        update cyvapay_links set currency = 'crys',
               amount     = round(amount / 10.0, 2),
               min_amount = greatest(round(min_amount / 10.0, 2), 0.01),
               max_amount = round(max_amount / 10.0, 2)
         where currency = 'aquilines';
    end if;
end $$;

-- The pufb / aquilines columns are now all zero and nothing reads them.
-- They're kept so this migration can be re-run safely; drop them later if
-- you like:  alter table cybucks drop column pufb, drop column aquilines;
