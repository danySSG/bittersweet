import type { Metadata } from "next";
import Link from "next/link";
import SiteFooter from "@/components/site-footer";

/**
 * SPEC v0.14 §C: /privacy — политика данных. Статическая страница
 * на русском: что собираем, как храним, как отключить. Честно и коротко,
 * без юридического тумана. Фундамент для верификации Google OAuth.
 */

export const metadata: Metadata = {
  title: "Bittersweet — политика данных",
  description:
    "Что Bittersweet делает с твоими данными: читаем только лайки, храним не дольше 30 дней, отключение в один клик.",
};

export default function PrivacyPage() {
  return (
    <main className="privacy-page container">
      <Link href="/" className="back-link">
        ← Bittersweet
      </Link>

      <h1>Политика данных</h1>
      <p className="privacy-meta mono">версия от 30 июля 2026</p>
      <p className="privacy-lead">
        Bittersweet строит акустический портрет твоего музыкального вкуса.
        Здесь — всё, что мы делаем с данными. Коротко и честно.
      </p>

      <section className="privacy-section">
        <h2>Что мы собираем</h2>
        <p>
          Когда ты входишь через Google, мы читаем только твои лайки в YouTube
          Music — через официальный YouTube API. Из каждого лайка берём три
          вещи: название трека, канал и дату.
        </p>
        <p>
          Мы <strong>не</strong> собираем: аудиофайлы, историю просмотров,
          подписки, плейлисты без твоей ссылки и любые личные данные сверх
          названия твоего канала.
        </p>
      </section>

      <section className="privacy-section">
        <h2>Как мы анализируем звук</h2>
        <p>
          Звук мы слушаем по 30-секундным превью из открытых каталогов iTunes
          и Deezer. Сами превью не сохраняем: прослушали — посчитали темп,
          тональность, яркость и энергию — оставили только эти числа.
        </p>
      </section>

      <section className="privacy-section">
        <h2>Сколько храним</h2>
        <p>
          Данные из YouTube API живут у нас не дольше 30 дней. Не обновил
          портрет за это время — они удаляются.
        </p>
      </section>

      <section className="privacy-section">
        <h2>Как отключить</h2>
        <p>
          Кнопка «Отключить» на странице портрета срабатывает сразу: мы
          отзываем доступ у Google, удаляем токены и все портреты, построенные
          по твоим лайкам. Без писем, форм и ожидания.
        </p>
      </section>

      <section className="privacy-section">
        <h2>Вопросы</h2>
        <p>
          Пиши: <a href="mailto:danyfomin003@gmail.com">danyfomin003@gmail.com</a>
        </p>
      </section>

      <SiteFooter />
    </main>
  );
}
