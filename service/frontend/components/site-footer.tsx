import Link from "next/link";

/**
 * SPEC v0.14 §C: футер с брендом и ссылкой на политику данных.
 * Показывается на лендинге и страницах портрета. GitHub-ссылки нет
 * (репозиторий пока приватный).
 */
export default function SiteFooter() {
  return (
    <footer className="site-footer">
      <span className="site-footer-brand mono">bittersweet</span>
      <span className="site-footer-sep" aria-hidden>
        ·
      </span>
      <Link href="/privacy" className="site-footer-link">
        политика данных
      </Link>
    </footer>
  );
}
