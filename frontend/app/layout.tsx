import type { Metadata, Viewport } from 'next';
import { Inter, JetBrains_Mono, Space_Grotesk } from 'next/font/google';
import './globals.css';
import { AuthProvider } from '../components/AuthProvider';
import { themeScript } from '../components/ThemeToggle';

const inter = Inter({
  subsets: ['latin'],
  display: 'swap',
  variable: '--font-inter'
});
const grotesk = Space_Grotesk({ subsets: ['latin'], display: 'swap', variable: '--font-grotesk' });
const mono = JetBrains_Mono({ subsets: ['latin'], display: 'swap', variable: '--font-jetbrains' });

export const metadata: Metadata = {
  title: {
    default: 'GridSense AI',
    template: '%s | GridSense AI'
  },
  description: 'Intelligent electricity demand and peak-load forecasting for the Delhi power grid.'
};

export const viewport: Viewport = {
  themeColor: '#081442',
  width: 'device-width',
  initialScale: 1
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className={`${inter.variable} ${grotesk.variable} ${mono.variable}`} suppressHydrationWarning>
      <head><script dangerouslySetInnerHTML={{ __html: themeScript }} /></head>
      <body><AuthProvider>{children}</AuthProvider></body>
    </html>
  );
}
