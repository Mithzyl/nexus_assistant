import { AppShell } from "@/components/mobile/app-shell";

export default function MobileLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return <AppShell>{children}</AppShell>;
}
