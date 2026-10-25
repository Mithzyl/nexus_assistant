import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "生活助手",
  description:
    "Personal Assistant 移动 Web：今天、事务、助手与我的。",
  applicationName: "生活助手",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  maximumScale: 5,
  viewportFit: "cover",
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#FAF9F4" },
    { media: "(prefers-color-scheme: dark)", color: "#111619" },
  ],
};

/**
 * Apply the stored appearance preference before paint so dark mode does not flash.
 * "system" (the default) removes the attribute and lets the media query win.
 */
const themeScript = `(function(){try{var raw=localStorage.getItem("pa.web.theme.choice");var t=raw?JSON.parse(raw):"system";if(t==="dark"||t==="light"){document.documentElement.setAttribute("data-theme",t);}else{document.documentElement.removeAttribute("data-theme");}}catch(e){}})();`;

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="zh-CN" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeScript }} />
      </head>
      <body>{children}</body>
    </html>
  );
}
