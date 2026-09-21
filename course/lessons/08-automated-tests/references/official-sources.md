# Официальные источники YAxUnit и Vanessa Automation

> Проверено при подготовке урока 21 сентября 2026 года. Страница релизов может измениться; перед публикацией учебного комплекта версии нужно перепроверить и пройти smoke-тесты заново.

## YAxUnit

- [официальная документация](https://bia-technologies.github.io/yaxunit/);
- [установка](https://bia-technologies.github.io/yaxunit/docs/getting-started/install/);
- [первый тест](https://bia-technologies.github.io/yaxunit/docs/getting-started/first-test/);
- [запуск и отладка](https://bia-technologies.github.io/yaxunit/docs/getting-started/run/);
- [официальный репозиторий](https://github.com/bia-technologies/yaxunit);
- [официальные релизы](https://github.com/bia-technologies/yaxunit/releases).

На дату проверки страница GitHub помечает `25.12` как последний релиз. Это не означает, что курс автоматически должен обновляться на него: используем только версию, прошедшую пилот с поддерживаемой платформой и Demo83.

Официальная инструкция требует платформу 8.3.10 или новее и после загрузки расширения предлагает отключить безопасный режим и защиту от опасных действий. В курсе это разрешено только для проверенного CFE в отдельной тестовой базе.

## Vanessa Automation

- [официальный репозиторий](https://github.com/Pr-Mex/vanessa-automation);
- [основная документация](https://pr-mex.github.io/vanessa-automation/dev/);
- [официальные релизы](https://github.com/Pr-Mex/vanessa-automation/releases);
- [параметры командного запуска](https://pr-mex.github.io/vanessa-automation/dev/CommandSetting/CommandSetting/).

На дату проверки последним релизом на GitHub указан `1.2.043.42`. Документация также описывает установку через OneScript Package Manager:

```powershell
opm install vanessa-automation
opm install vanessa-automation-single
```

Вариант `VASingle` поставляется одним EPF и сохраняет основную функциональность обычной сборки. Для первого локального урока он уменьшает число частей окружения.

Не используй старый проект Vanessa-ADD как источник актуального дистрибутива Vanessa Automation. Не скачивай перепакованные EPF и CFE из случайных публикаций.

## Что фиксировать рядом с дистрибутивом

Для каждого файла:

- точную версию;
- URL страницы релиза;
- имя asset;
- дату скачивания;
- SHA-256;
- версию платформы, на которой пройден smoke;
- результат smoke и путь к отчёту.

Сам бинарный файл не нужно автоматически добавлять в Git. Решение о распространении CFE/EPF в комплекте курса принимается отдельно после проверки лицензий и процедуры обновления.
