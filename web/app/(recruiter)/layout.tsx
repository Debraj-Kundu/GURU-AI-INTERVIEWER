import Link from 'next/link';
import { ThemeButton } from '@/components/app/theme-toggle';
import { Logo } from '@/components/guru/ui';

// Recruiter area. ponytail: no sign-in yet (PLAN D2 magic link comes later); local dev only.
export default function RecruiterLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-svh">
      <header className="bg-background/70 sticky top-0 z-40 border-b backdrop-blur-xl">
        <nav className="mx-auto flex h-14 max-w-[1600px] items-center gap-6 px-4 lg:px-6">
          <Link href="/" className="flex items-center gap-2.5">
            <Logo className="size-7" />
            <span className="text-lg font-semibold tracking-tight">Guru</span>
          </Link>
          <span className="bg-border hidden h-5 w-px sm:block" />
          <Link
            href="/"
            className="text-muted-foreground hover:text-foreground hidden text-sm font-medium transition-colors sm:block"
          >
            Companies
          </Link>
          <div className="ml-auto flex items-center gap-3">
            <span className="text-muted-foreground hidden items-center gap-1.5 text-xs md:flex">
              <span className="size-1.5 rounded-full bg-emerald-500 shadow-[0_0_0_3px] shadow-emerald-500/20" />
              AI voice interviews
            </span>
            <ThemeButton />
          </div>
        </nav>
      </header>
      <main className="mx-auto max-w-[1600px] px-4 py-8 lg:px-6">{children}</main>
    </div>
  );
}
