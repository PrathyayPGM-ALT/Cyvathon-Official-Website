-- ============================================================
--  Cyvathon — Armoury divisions
--  The Armoury takes more than G2 pens now: rubber-band artillery, paper
--  ordnance, foam blasters, the Water Corps, parade colours and more, each
--  paying its own rate. This adds the `division` column that records which
--  division a handover belongs to. Everything already logged in stays a Pen
--  Launcher handover, which is what it was.
--  Safe & idempotent. Paste into the Supabase SQL editor and run.
-- ============================================================

alter table pen_donations add column if not exists division text default 'launchers';

-- Anything filed before divisions existed was a pen handover.
update pen_donations set division = 'launchers' where division is null;
