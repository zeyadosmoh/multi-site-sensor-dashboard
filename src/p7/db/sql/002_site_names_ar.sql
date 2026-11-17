-- Arabic display names for sites and cities (bilingual UI and reports). NULL falls back to the English name.
ALTER TABLE sites ADD COLUMN name_ar text, ADD COLUMN city_ar text;
