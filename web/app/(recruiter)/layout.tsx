import Link from 'next/link';
import { ThemeButton } from '@/components/app/theme-toggle';

// Recruiter area. ponytail: no sign-in yet (PLAN D2 magic link comes later); local dev only.
export default function RecruiterLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-svh">
      <header className="bg-background/85 border-border/60 sticky top-0 z-40 border-b backdrop-blur-lg">
        <nav className="mx-auto flex h-16 max-w-[1600px] items-center gap-8 px-4">
          <Link href="/" className="text-2xl font-extrabold tracking-[-0.065em]">
            guru<span className="text-primary">.</span>
          </Link>
          <Link
            href="/"
            className="after:bg-primary relative font-mono text-xs tracking-widest uppercase after:absolute after:-bottom-1.5 after:left-0 after:h-px after:w-0 after:transition-all hover:after:w-full"
          >
            Companies
          </Link>
          <div className="ml-auto">
            <ThemeButton />
          </div>
        </nav>
      </header>
      <main className="animate-in fade-in slide-in-from-bottom-2 mx-auto max-w-[1600px] px-4 py-10 duration-500 motion-reduce:animate-none">
        {children}
      </main>
    </div>
  );
}
