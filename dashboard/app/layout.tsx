import type { Metadata } from 'next';
import './globals.css';

export const metadata: Metadata = {
  title: '我的一天 · Kindle 时间 Dashboard',
  description: '一眼看清今天如何度过，以及还剩多少可以自己安排的时间。',
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="zh-CN">
      <head>
        <meta httpEquiv="refresh" content="300" />
      </head>
      <body>{children}</body>
    </html>
  );
}

