import Link from "next/link";
import { YOUTUBE_LOGIN_URL } from "@/lib/api";
import SiteFooter from "@/components/site-footer";
import HeroGalaxy from "./hero-galaxy";
import PlaylistForm from "./playlist-form";
import TakeoutUpload from "./takeout-upload";

const STEPS = [
  {
    title: "Ссылка",
    text: "Вставь плейлист YouTube Music — публичный или «не в списке» — или открой демо без логина.",
  },
  {
    title: "Анализ звука",
    text: "Слушаем 30-секундные превью треков: темп, тональность, яркость, энергию — сам звук, не теги.",
  },
  {
    title: "Портрет",
    text: "Карта настроений, архетипы вкуса, биттерсвит и постоянная ссылка, чтобы сравнить вкус с другом.",
  },
];

export default function LandingPage() {
  return (
    <main>
      <section className="hero">
        {/* v0.9 §D: живая галактика демо-портрета за hero-текстом
            (при недоступном бэке — прежние CSS-точки) */}
        <HeroGalaxy />
        {/* v0.9 §C: шапка лендинга ведёт на «Мои портреты» */}
        <Link href="/portraits" className="hero-portraits-link">
          Мои портреты →
        </Link>
        {/* v0.10 §C: радиальная виньетка-подложка (scrim) под текстовым
            блоком — контраст текста железный при любом положении звёзд */}
        <div className="hero-scrim">
          <div className="hero-brand">Bittersweet</div>
          <h1>
            Акустический портрет <em>твоего вкуса</em>
          </h1>
          <p className="hero-sub">
            Мы анализируем звук твоих любимых треков — темп, тональность,
            яркость, энергию — и рисуем карту настроений: кластеры вкуса, доля
            биттерсвита и твоя акустическая сигнатура.
          </p>
          <p className="hero-sub hero-sub-second">
            Архетипы вкуса, карта настроений и совместимость — по реальному
            звуку, а не тегам.
          </p>
          <div className="hero-actions">
            <a href={YOUTUBE_LOGIN_URL} className="btn btn-primary">
              Войти через YouTube
            </a>
            <Link href="/portrait?demo=1" className="btn btn-ghost">
              Попробовать демо
            </Link>
          </div>
          <p className="hero-login-note">
            официальный вход Google · читаем только лайки · отключение в один
            клик
          </p>
        </div>
        <PlaylistForm />
        <TakeoutUpload />
        <p className="hero-note">
          Демо строится по готовому набору из 448 треков — без логина. Плейлист
          YouTube Music должен быть публичным или «не в списке».
        </p>
      </section>

      <section className="how container">
        <h2 className="section-title">Как это работает</h2>
        <ol className="how-steps">
          {STEPS.map((step, i) => (
            <li className="how-step" key={step.title}>
              <span className="how-num mono">{i + 1}</span>
              <h3>{step.title}</h3>
              <p>{step.text}</p>
            </li>
          ))}
        </ol>
      </section>

      <SiteFooter />
    </main>
  );
}
