-- LGD Tail Extension: the reference-curve shape is replaced by the log-normal shape (9 October 2026).
--
-- Method numbers are unchanged: a scenario or override stored with method 3 keeps it and now
-- means log-normal. The retired parameter client_cohort is removed from stored parameter sets,
-- and every result whose effective method was 3 is marked out of date, because its stored
-- figures were computed with the reference curve. Reference-shape curve rows (kind = 'shape')
-- are left in client_curves, unused. The app also applies these changes itself at start-up
-- (hazard_ext.web.runner.migrate_three_methods), so running this file is optional on Supabase
-- and not needed on SQLite. Safe to run again.

update hazard.scenarios set params = params - 'client_cohort' where params ? 'client_cohort';
update hazard.scenario_overrides set params = params - 'client_cohort' where params ? 'client_cohort';
update hazard.results set effective_params = effective_params - 'client_cohort'
  where effective_params ? 'client_cohort';
update hazard.results set stale = true
  where status = 'ok' and (effective_params ->> 'method') = '3' and (payload -> 'averages') ? 'lgd_client';
