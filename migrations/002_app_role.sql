-- LGD Tail Extension: rights for the application's own database role.
--
-- The app connects as "hazard_app", not as "postgres". That role can read and write rows in
-- the hazard schema and nothing else: it cannot change tables, and it has no rights on any
-- other schema. scripts/supabase_setup.py creates the role and sets its password, then runs
-- this file. Safe to run again.
--
-- Row-level security stays on. Each table gets one policy that admits hazard_app only, so the
-- Supabase API roles (anon, authenticated) still see nothing.

grant usage on schema hazard to hazard_app;
grant select, insert, update, delete on all tables in schema hazard to hazard_app;
grant usage, select on all sequences in schema hazard to hazard_app;

-- tables added by later migrations (run as postgres) get the same rights
alter default privileges for role postgres in schema hazard
  grant select, insert, update, delete on tables to hazard_app;
alter default privileges for role postgres in schema hazard
  grant usage, select on sequences to hazard_app;

do $$
declare t text;
begin
  for t in select tablename from pg_tables where schemaname = 'hazard' loop
    execute format('drop policy if exists app_all on hazard.%I', t);
    execute format(
      'create policy app_all on hazard.%I for all to hazard_app using (true) with check (true)', t);
  end loop;
end $$;
