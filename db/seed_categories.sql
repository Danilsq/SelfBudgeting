-- Starter category tree. Adjust freely -- this just gives the rule-based
-- categorizer (stage 1) and the LLM refiner (a later stage) somewhere to
-- point at. Names on the left are normalized categories you'll actually
-- use in analysis; "kind" separates expenses from income from transfers
-- (internal moves between your own accounts, which should usually be
-- excluded from expense/income totals).

INSERT INTO categories (name, kind) VALUES
    ('Продукты', 'expense'),
    ('Рестораны и кафе', 'expense'),
    ('Транспорт', 'expense'),
    ('Такси', 'expense'),
    ('Здоровье и красота', 'expense'),
    ('Одежда и обувь', 'expense'),
    ('Развлечения', 'expense'),
    ('Связь и интернет', 'expense'),
    ('Жильё и коммунальные услуги', 'expense'),
    ('Образование', 'expense'),
    ('Путешествия', 'expense'),
    ('Подписки', 'expense'),
    ('Прочие расходы', 'expense'),
    ('Зарплата', 'income'),
    ('Проценты по вкладам', 'income'),
    ('Кэшбэк', 'income'),
    ('Прочие доходы', 'income'),
    ('Перевод между своими счетами', 'transfer'),
    ('Погашение кредита/процентов', 'internal'),
    ('Капитализация/пролонгация вклада', 'internal')
ON CONFLICT (name) DO NOTHING;
