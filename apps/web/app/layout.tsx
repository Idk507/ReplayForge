import type { Metadata } from "next";
import "./globals.css";
export const metadata: Metadata = {title: "ReplayForge · Investigation workbench", description: "Evidence-backed incident repair. Local development workbench."};
export default function Layout({children}: Readonly<{children: React.ReactNode}>) {
  return <html lang="en"><body><a className="skip" href="#main">Skip to investigation</a>{children}</body></html>;
}
