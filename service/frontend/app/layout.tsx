import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Bittersweet — акустический портрет твоего вкуса",
  description:
    "Карта настроений твоей музыки: архетипы, биттерсвит и акустическая сигнатура — по 30-секундным превью твоих любимых треков.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="ru">
      <body>{children}</body>
    </html>
  );
}
