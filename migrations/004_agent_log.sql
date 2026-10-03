-- LGD Tail Extension: log of the assistant's instructions, replies and proposals (3 October 2026).
-- Safe to run again. Rights for hazard_app follow from the default privileges set in 002.

create table if not exists hazard.agent_log (
  id serial not null,
  project_id integer not null references hazard.projects (id) on delete cascade,
  user_id integer not null references hazard.users (id),
  instruction text not null,
  reply text not null,
  proposals jsonb not null,
  applied boolean not null,
  applied_at timestamp with time zone,
  created_at timestamp with time zone not null,
  primary key (id)
);
create index if not exists ix_hazard_agent_log_project_id on hazard.agent_log (project_id);
alter table hazard.agent_log enable row level security;
drop policy if exists app_all on hazard.agent_log;
create policy app_all on hazard.agent_log for all to hazard_app using (true) with check (true);
grant select, insert, update, delete on hazard.agent_log to hazard_app;
grant usage, select on sequence hazard.agent_log_id_seq to hazard_app;
