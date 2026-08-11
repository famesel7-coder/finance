const $ = (selector) => document.querySelector(selector);

const factorLabels = {
  positive_10d_momentum: "импульс 10 дней",
  above_20d_average: "выше средней",
  volume_expansion: "рост объёма",
  overbought_risk: "риск перегрева",
};

const money = new Intl.NumberFormat("ru-RU", {
  style: "currency",
  currency: "RUB",
  maximumFractionDigits: 0,
});

const number = new Intl.NumberFormat("ru-RU", { maximumFractionDigits: 2 });

function requestBody(period = $("#period").value) {
  return {
    tickers: [],
    market: "moex",
    period,
    top_n: Number($("#top-n").value),
  };
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const payload = await response.json();
  if (!response.ok) throw new Error(payload.detail || "Сервис вернул ошибку");
  return payload;
}

function setBusy(busy, message = "") {
  $("#analyze").disabled = busy;
  $("#backtest").disabled = busy;
  $("#status").className = "status";
  $("#status").textContent = message;
}

function renderSignals(payload) {
  $("#horizon").textContent = `Горизонт модели: ${payload.horizon_days} торговых дней`;
  $("#signals-body").innerHTML = payload.signals
    .map((signal, index) => {
      const probability = Math.round(signal.probability_up * 100);
      const factors = signal.factors.length
        ? signal.factors.map((item) => `<span class="tag">${factorLabels[item] || item}</span>`).join("")
        : '<span class="tag">нейтральный сигнал</span>';
      return `<tr>
        <td>${String(index + 1).padStart(2, "0")}</td>
        <td><span class="company">${signal.company}<small>${signal.ticker}</small></span></td>
        <td>${signal.sector}</td>
        <td><span class="probability"><span class="bar"><i style="width:${probability}%"></i></span><strong>${probability}%</strong></span></td>
        <td>${number.format(signal.last_price)} ₽</td>
        <td>${number.format(signal.volatility_20d * 100)}%</td>
        <td>${factors}</td>
      </tr>`;
    })
    .join("");
}

function renderPortfolio(portfolio) {
  const investedPct = Math.round((portfolio.invested_amount / portfolio.capital) * 100);
  $("#invested").textContent = money.format(portfolio.invested_amount);
  $("#reserve").textContent = money.format(portfolio.cash_reserve);
  $("#invested-pct").textContent = `${investedPct}%`;
  $("#donut").style.setProperty("--invested", `${investedPct}%`);
  $("#positions").innerHTML = portfolio.positions
    .map(
      (position) => `<div class="position-row">
        <strong>${position.ticker}<small>${position.company}</small></strong>
        <span>${money.format(position.target_amount)}<small>целевой объём</small></span>
        <span>${position.estimated_units}<small>примерно, шт.</small></span>
        <strong class="weight">${number.format(position.weight * 100)}%</strong>
      </div>`,
    )
    .join("");
}

async function analyze() {
  const capital = Number($("#capital").value);
  if (!Number.isFinite(capital) || capital <= 0) {
    $("#status").className = "status error";
    $("#status").textContent = "Введите положительную сумму капитала.";
    return;
  }
  setBusy(true, "Загружаем котировки MOEX и рассчитываем сигналы…");
  try {
    const body = requestBody();
    const analysis = await api("/analysis", {
      method: "POST",
      body: JSON.stringify({
        ...body,
        capital,
        risk_profile: $("#risk-profile").value,
      }),
    });
    renderSignals(analysis);
    renderPortfolio(analysis.portfolio);
    $("#results").classList.remove("hidden");
    $("#backtest-result").classList.add("hidden");
    setBusy(false, `Готово. Проанализировано ${analysis.signals.length} лучших кандидатов из ликвидной выборки.`);
    $("#results").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    setBusy(false, "");
    $("#status").className = "status error";
    $("#status").textContent = `Не удалось выполнить анализ: ${error.message}`;
  }
}

async function runBacktest() {
  setBusy(true, "Запускаем хронологический тест на данных, которых модель не видела…");
  try {
    const result = await api("/backtest", {
      method: "POST",
      body: JSON.stringify(requestBody("5y")),
    });
    const metrics = [
      ["Общая доходность", result.total_return, "percent"],
      ["Годовая оценка", result.annualized_return, "percent"],
      ["Волатильность", result.annualized_volatility, "percent"],
      ["Доля прибыльных", result.win_rate, "percent"],
      ["Макс. просадка", result.max_drawdown, "percent"],
      ["Коэффициент Шарпа", result.sharpe_ratio, "number"],
    ];
    $("#backtest-result").innerHTML = metrics
      .map(([label, value, kind]) => {
        const display = kind === "percent" ? `${number.format(value * 100)}%` : number.format(value);
        const tone = value > 0 ? "positive" : value < 0 ? "negative" : "";
        return `<div class="metric"><span>${label}</span><strong class="${tone}">${display}</strong></div>`;
      })
      .join("");
    $("#backtest-result").classList.remove("hidden");
    setBusy(false, `Бэктест готов: ${result.observations} неперекрывающихся периодов. Прошлые результаты не гарантируют будущих.`);
  } catch (error) {
    setBusy(false, "");
    $("#status").className = "status error";
    $("#status").textContent = `Бэктест не выполнен: ${error.message}`;
  }
}

async function loadBrokerStatus() {
  try {
    const status = await api("/broker/status");
    const badge = $("#broker-badge");
    if (status.configured) {
      badge.textContent = status.sandbox ? "Sandbox настроен" : "Боевой счёт настроен";
      badge.classList.add("ok");
      $("#broker-copy").textContent = status.live_trading_enabled
        ? "Отправка подтверждённых заявок разрешена конфигурацией."
        : "Ключ подключён, реальные заявки заблокированы настройкой безопасности.";
    } else {
      $("#broker-copy").textContent = "Добавьте токен и идентификатор счёта в .env. Секреты не передаются в браузер.";
    }
  } catch (_) {
    $("#broker-copy").textContent = "Статус брокера временно недоступен.";
  }
}

$("#analyze").addEventListener("click", analyze);
$("#backtest").addEventListener("click", runBacktest);
loadBrokerStatus();
