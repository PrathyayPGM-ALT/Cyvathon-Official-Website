-- ============================================================
--  Cyvathon — Chancellor & Vice President
--  The Prime Minister's office becomes the Chancellor's, held by
--  Arjun Soni, and a Vice President is added, held by Srikrish.
--  Safe & idempotent. Paste into the Supabase SQL editor and run.
--  Check the "Messages" tab afterwards: it says if either name
--  didn't match a citizen account.
-- ============================================================

do $$
declare
    chancellor text;
    vp         text;
begin
    -- Find each account whatever the capitalisation, so the seat and the
    -- account name always match exactly.
    select username into chancellor from cybucks where lower(username) = lower('Arjun Soni') limit 1;
    select username into vp         from cybucks where lower(username) = lower('Srikrish')   limit 1;
    if chancellor is null then
        raise notice 'No citizen account named "Arjun Soni" — the Chancellor seat shows that name, but no one is paid for it. Check the spelling.';
        chancellor := 'Arjun Soni';
    end if;
    if vp is null then
        raise notice 'No citizen account named "Srikrish" — the Vice President seat shows that name, but no one is paid for it. Check the spelling.';
        vp := 'Srikrish';
    end if;

    -- 1. The Prime Minister's seat becomes the Chancellor's (keeping its place).
    if not exists (select 1 from government where position = 'Chancellor') then
        update government set position = 'Chancellor' where position = 'Prime Minister';
    end if;
    delete from government where position = 'Prime Minister';
    insert into government (position, holder, rank) values ('Chancellor', chancellor, 2)
        on conflict (position) do update set holder = excluded.holder;

    -- 2. The Vice President sits just below the President. Make room once.
    if not exists (select 1 from government where position = 'Vice President') then
        update government set rank = rank + 1 where rank >= 2;
        insert into government (position, holder, rank) values ('Vice President', vp, 2);
    else
        update government set holder = vp where position = 'Vice President';
    end if;

    -- 3. Titles, which is also what sets pay (1,000 CB a week for each).
    --    Anyone still titled Prime Minister goes back to Citizen.
    update cybucks set designation = 'Citizen'        where designation = 'Prime Minister';
    update cybucks set designation = 'Chancellor'     where username = chancellor;
    update cybucks set designation = 'Vice President' where username = vp;

    -- 4. A vote still open for Prime Minister now elects a Chancellor.
    update polls set position = 'Chancellor' where position = 'Prime Minister' and open;

    -- 5. On their records, and in their notifications — once each.
    if not exists (select 1 from records where username = chancellor and entry like 'Named Chancellor%') then
        insert into records (username, entry)
            values (chancellor, 'Named Chancellor of Cyvathon by the President.');
        insert into notifications (username, message, link)
            values (chancellor, '🏛️ You are now Chancellor of Cyvathon — you lead the Cabinet.', '/government');
    end if;
    if not exists (select 1 from records where username = vp and entry like 'Named Vice President%') then
        insert into records (username, entry)
            values (vp, 'Named Vice President of Cyvathon by the President.');
        insert into notifications (username, message, link)
            values (vp, '🏛️ You are now Vice President of Cyvathon.', '/government');
    end if;
end $$;

alter table polls alter column position set default 'Chancellor';
