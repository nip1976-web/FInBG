-- Опознание клиента перестаёт зависеть от того, как записано название.
--
-- Николай форматирует названия в файлах так, чтобы их было удобно читать
-- менеджерам: организационно-правовая форма сокращается и уходит в конец —
-- «Ромашка ООО», а не «Общество с ограниченной ответственностью "Ромашка"».
-- Это правильно, но программа сравнивала названия посимвольно, и любая
-- перестановка означала бы другого клиента со всеми последствиями: сделки
-- заводятся заново, привязанные платежи остаются на старой карточке.
--
-- Теперь перед сравнением отбрасываются форма собственности, кавычки, знаки
-- препинания и регистр. «Ромашка ООО», «ООО Ромашка» и «ООО «Ромашка»» —
-- один клиент.
--
-- Проверено перед установкой: 121 название дало 121 разный ключ, ни один
-- клиент случайно не склеивается с другим.
--
-- Чего это не покрывает: сокращение против полного написания. «Приангарский
-- ЛПК» и «Приангарский Лесоперерабатывающий Комплекс» — разные слова, никакая
-- формула их не свяжет. Для таких случаев ниже заводится список
-- альтернативных написаний, который пополняется с подтверждения человека.

begin;

-- Повторяет normalize_name из api/scripts/import_buyers_deals.py.
-- Две реализации обязаны совпадать: разойдутся — загрузчик и база будут
-- по-разному отвечать на вопрос «это тот же клиент?».
create or replace function normalize_counterparty_name(value text)
returns text
language sql
immutable
as $$
    select btrim(
        regexp_replace(
            regexp_replace(
                regexp_replace(lower(coalesce(value, '')), '[«»"''.,()№]', ' ', 'g'),
                '\m(ооо|зао|оао|пао|ип|ао|филиал)\M', ' ', 'g'
            ),
            '[^a-zа-яё0-9]+', ' ', 'g'
        )
    );
$$;

-- Два клиента, чьи названия сходятся после нормализации, — это одна фирма,
-- записанная дважды. База такого больше не допустит.
create unique index if not exists counterparties_normalized_name_uidx
    on counterparties (normalize_counterparty_name(name));

create table if not exists counterparty_name_aliases (
    id              bigint generated always as identity primary key,
    counterparty_id bigint not null references counterparties (id) on delete cascade,
    alias_name      text not null,
    note            text,
    created_at      timestamptz not null default now()
);

comment on table counterparty_name_aliases is
    'Альтернативные написания названия клиента: сокращение против полного, '
    'старое название против нового. Заводится только с подтверждения человека.';

-- Одно написание не может принадлежать двум клиентам, иначе опознание
-- становится гаданием.
create unique index if not exists counterparty_name_aliases_uidx
    on counterparty_name_aliases (normalize_counterparty_name(alias_name));

-- Написание не должно спорить с названием существующей карточки.
create or replace function counterparty_name_alias_is_free()
returns trigger
language plpgsql
as $$
begin
    if exists (
        select 1 from counterparties c
        where normalize_counterparty_name(c.name)
              = normalize_counterparty_name(new.alias_name)
          and c.id <> new.counterparty_id
    ) then
        raise exception
            'Написание «%» уже занято другой карточкой клиента', new.alias_name;
    end if;
    return new;
end;
$$;

drop trigger if exists counterparty_name_aliases_check on counterparty_name_aliases;
create trigger counterparty_name_aliases_check
    before insert or update on counterparty_name_aliases
    for each row execute function counterparty_name_alias_is_free();

-- Первое написание: подтверждено Николаем 03.08.2026 при слиянии карточек.
-- В Buyers.xlsx строка со спецификацией 55 записана длинным вариантом.
insert into counterparty_name_aliases (counterparty_id, alias_name, note)
select c.id,
       'Приангарский Лесоперерабатывающий Комплекс ООО',
       'Длинное написание из Buyers.xlsx; подтверждено 03.08.2026'
from counterparties c
where c.tax_id = '2463223960'
  and not exists (
      select 1 from counterparty_name_aliases a
      where normalize_counterparty_name(a.alias_name)
            = normalize_counterparty_name('Приангарский Лесоперерабатывающий Комплекс ООО')
  );

commit;
