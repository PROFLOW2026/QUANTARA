import type { Metadata, Viewport } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import { ThemeProvider, STORAGE_KEY } from "@/components/theme/ThemeProvider";
import { PwaBootstrap } from "@/components/pwa/PwaBootstrap";
import "./globals.css";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-sans",
});

const jetbrainsMono = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-mono",
});

export const metadata: Metadata = {
  title: "QUANTARA",
  description: "מסחר אלגוריתמי — סימולציה ובדיקות היסטוריות",
  applicationName: "QUANTARA",
  appleWebApp: {
    capable: true,
    title: "QUANTARA",
    statusBarStyle: "black-translucent",
  },
  icons: {
    icon: [{ url: "/icons/icon-192.png", sizes: "192x192", type: "image/png" }],
    apple: [{ url: "/icons/icon-192.png", sizes: "192x192", type: "image/png" }],
  },
};

export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f1f5f9" },
    { media: "(prefers-color-scheme: dark)", color: "#0a0e17" },
  ],
  viewportFit: "cover",
};

const themeInitScript = `(function(){try{var k=${JSON.stringify(STORAGE_KEY)};var t=localStorage.getItem(k);var r=document.documentElement;if(t==='light'){r.classList.remove('dark');r.classList.add('light')}else{r.classList.add('dark');r.classList.remove('light')}}catch(e){document.documentElement.classList.add('dark')}})();`;

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="he" dir="rtl" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeInitScript }} />
      </head>
      <body className={`${inter.variable} ${jetbrainsMono.variable} font-sans`}>
        <ThemeProvider>
          <PwaBootstrap />
          {children}
        </ThemeProvider>
      </body>
    </html>
  );
}
