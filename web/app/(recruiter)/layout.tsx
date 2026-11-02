import Link from 'next/link';

// Recruiter area. ponytail: no sign-in yet (PLAN D2 magic link comes later); local dev only.
export default function RecruiterLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-svh">
      <header className="bg-background/80 sticky top-0 z-40 border-b backdrop-blur">
        <nav className="mx-auto flex h-14 max-w-6xl items-center gap-6 px-4">
          <Link href="/jobs" className="font-mono text-sm font-bold tracking-wider uppercase">
            Guru
          </Link>
          <Link href="/jobs" className="text-muted-foreground hover:text-foreground text-sm">
            Jobs
          </Link>
        </nav>
      </header>
      <main className="mx-auto max-w-6xl px-4 py-8">{children}</main>
    </div>
  );
}
