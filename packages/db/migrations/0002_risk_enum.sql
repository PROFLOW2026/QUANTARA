-- Add competition risk enum values (must commit before use)

DO $$ BEGIN
  ALTER TYPE risk_profile_slug ADD VALUE 'very_conservative';
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
  ALTER TYPE risk_profile_slug ADD VALUE 'very_aggressive';
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;
