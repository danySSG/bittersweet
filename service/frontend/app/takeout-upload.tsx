"use client";

import { useCallback, useState } from "react";
import { useRouter } from "next/navigation";
import { ApiError, startTakeoutAnalysis } from "@/lib/api";

const MAX_SIZE_MB = 100;

/** Клиентская проверка файла до отправки: расширение + размер. */
function validateFile(file: File): string | null {
  const name = file.name.toLowerCase();
  if (!name.endsWith(".zip") && !name.endsWith(".csv")) {
    return "Нужен .zip из Google Takeout или отдельный .csv из него.";
  }
  if (file.size > MAX_SIZE_MB * 1024 * 1024) {
    return `Файл больше ${MAX_SIZE_MB} МБ. Выбери в Takeout только «музыкальная библиотека» и «плейлисты» — такой архив весит мегабайты.`;
  }
  return null;
}

/** Человеческое сообщение об ошибке загрузки. */
function describeUploadError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 413) {
      return `Файл слишком большой — бэкенд принимает до ${MAX_SIZE_MB} МБ. Оставь в Takeout только музыкальные данные.`;
    }
    if (err.status === 404 || err.status === 405) {
      return "Бэкенд пока не умеет принимать Takeout — обнови его до v0.2b и перезапусти.";
    }
    // 422 приходит с человеческим detail, сеть — с понятным сообщением
    return err.message;
  }
  return err instanceof Error ? err.message : "Не удалось загрузить файл.";
}

/**
 * Секция «Или загрузи выгрузку Google Takeout» на лендинге (v0.2b):
 * drag&drop-зона + file input (.zip/.csv) → POST multipart →
 * редирект на /portrait?job={id}, где страница поллит джобу.
 */
export default function TakeoutUpload() {
  const router = useRouter();
  const [dragOver, setDragOver] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [hint, setHint] = useState<string | null>(null);

  const upload = useCallback(
    (file: File) => {
      const problem = validateFile(file);
      if (problem !== null) {
        setHint(problem);
        return;
      }
      setHint(null);
      setUploading(true);
      startTakeoutAnalysis(file)
        .then(({ job_id }) => {
          router.push(`/portrait?job=${encodeURIComponent(job_id)}`);
        })
        .catch((err: unknown) => {
          setHint(describeUploadError(err));
          setUploading(false);
        });
      // при успехе uploading не сбрасываем — уходим со страницы
    },
    [router],
  );

  return (
    <div className="takeout-block">
      <div className="playlist-label">Или загрузи выгрузку Google Takeout</div>
      <label
        className={`dropzone${dragOver ? " dropzone-over" : ""}${
          uploading ? " dropzone-busy" : ""
        }`}
        onDragOver={(event) => {
          event.preventDefault();
          if (!uploading) setDragOver(true);
        }}
        onDragLeave={(event) => {
          if (!event.currentTarget.contains(event.relatedTarget as Node)) {
            setDragOver(false);
          }
        }}
        onDrop={(event) => {
          event.preventDefault();
          setDragOver(false);
          if (uploading) return;
          const file = event.dataTransfer.files[0];
          if (file !== undefined) upload(file);
        }}
      >
        <input
          type="file"
          accept=".zip,.csv"
          className="dropzone-input"
          aria-label="Файл выгрузки Google Takeout (.zip или .csv)"
          disabled={uploading}
          onChange={(event) => {
            const file = event.target.files?.[0];
            // сбрасываем value, чтобы повторный выбор того же файла сработал
            event.target.value = "";
            if (file !== undefined) upload(file);
          }}
        />
        {uploading ? (
          <span className="dropzone-text">
            <span className="btn-spinner" aria-hidden /> Загружаем выгрузку…
          </span>
        ) : (
          <>
            <span className="dropzone-text">
              Перетащи сюда <strong>.zip</strong> из Takeout или отдельный{" "}
              <strong>.csv</strong>
            </span>
            <span className="dropzone-sub">
              …или нажми, чтобы выбрать файл — до {MAX_SIZE_MB} МБ
            </span>
          </>
        )}
      </label>
      {hint !== null && (
        <p className="playlist-hint" role="alert">
          {hint}
        </p>
      )}
      <details className="takeout-details">
        <summary>Как получить выгрузку</summary>
        <ol className="takeout-steps">
          <li>
            Открой{" "}
            <a
              href="https://takeout.google.com"
              target="_blank"
              rel="noreferrer"
            >
              takeout.google.com
            </a>{" "}
            и нажми «Снять выбор со всех».
          </li>
          <li>Отметь только «YouTube и YouTube Music».</li>
          <li>
            В настройках контента («Все данные YouTube включены») оставь только
            «музыкальная библиотека» и «плейлисты».
          </li>
          <li>Создай экспорт и скачай zip — обычно это занимает минуты.</li>
          <li>Загрузи zip сюда целиком — распаковывать не нужно.</li>
        </ol>
      </details>
    </div>
  );
}
