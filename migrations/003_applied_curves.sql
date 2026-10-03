-- LGD Tail Extension: client applied recovery curves (3 October 2026).
--
-- client_curves gains two columns:
--   kind   'shape'   = replaces the built-in method 3 tail shape of the same label (existing rows)
--          'applied' = the client's applied recovery curve, drawn on the charts for comparison only
--   basis  'face'        = monthly rates are a share of the balance at default
--          'outstanding' = monthly rates are a share of the balance still outstanding each month
-- The unique key becomes (project_id, label, kind). Safe to run again.

alter table hazard.client_curves add column if not exists kind  varchar(20) not null default 'shape';
alter table hazard.client_curves add column if not exists basis varchar(20) not null default 'face';

alter table hazard.client_curves drop constraint if exists uq_curve_project_label;
alter table hazard.client_curves drop constraint if exists uq_curve_project_label_kind;
alter table hazard.client_curves add constraint uq_curve_project_label_kind unique (project_id, label, kind);
