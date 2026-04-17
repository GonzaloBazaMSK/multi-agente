import { Rail } from "@/components/layout/rail";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-screen overflow-hidden">
      <Rail />
      <main className="flex-1 flex overflow-hidden">{children}</main>
    </div>
  );
}
