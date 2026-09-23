-- Preserve the imported baseline independently from locally changed career goals.
ALTER TABLE employee_profiles ADD COLUMN source_json TEXT;
UPDATE employee_profiles SET source_json = profile_json;
