-- 1. Create the Analyses Table
create table public.analyses (
  id uuid default gen_random_uuid() primary key,
  user_id uuid references auth.users not null,
  player_number text,
  team text,
  jersey_color text,
  analysis_text text,
  created_at timestamptz default now()
);

-- 2. Enable Row Level Security (RLS)
alter table public.analyses enable row level security;

-- 3. Policy: Users can VIEW their own analysis
create policy "Users can view their own analyses"
on public.analyses for select
using (auth.uid() = user_id);

-- 4. Policy: Service Role (Backend) can ALL, and Users can INSERT (if needed directly)
-- Note: The Python Backend should use the Service Role Key to bypass this, 
-- but if using Anon Key, we might need an insert policy.
-- For now, we trust the Backend to handle the insert with Service Role.
