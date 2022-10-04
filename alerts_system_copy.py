import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
import io
import pandas as pd
import pandahouse as ph
import telegram
import datetime
from prophet import Prophet

def get_data():
    connection = {'host': 'https://clickhouse.lab.karpov.courses',
                      'database': 'simulator_20220820',
                      'user': 'student',
                      'password': 'dpo_python_2020'
                      }

    # загружаем данные за прошлые 14 дней и за сегодня
    query = '''select *
    from
    (SELECT
                                  toStartOfFifteenMinutes(time) as ts, 
                                  toDate(ts) as date, 
                                  formatDateTime(ts, '%R') as hm, 
                                  uniqExact(user_id) as DAU_feed,
                                  countIf(action = 'like') as likes, 
                                  countIf(action='view') as views, 
                                  likes / views as CTR
                            FROM simulator_20220820.feed_actions
                            WHERE ts >=  today() - 14 and ts < toStartOfFifteenMinutes(now())
                            GROUP BY ts, date, hm
                            ORDER BY ts) t1
    join
    (SELECT
                                  toStartOfFifteenMinutes(time) as ts, 
                                  count(distinct user_id) as DAU_messages,
                                  count(user_id) as messages_sent
                                  FROM simulator_20220820.message_actions
                                          WHERE ts >=  today() - 14 and ts < toStartOfFifteenMinutes(now())
                            GROUP BY ts
                            ORDER BY ts) t2
    using ts'''

    data = ph.read_clickhouse(query, connection=connection)
    return data

def check_anomaly(df, metric, threshold=0.3):
    # функция check_anomaly предлагает алгоритм проверки значения на аномальность посредством
    # сравнения интересующего значения со значением в это же время сутки назад
    # при желании алгоритм внутри этой функции можно изменить
    current_ts = df['ts'].max()  # достаем максимальную 15-минутку из датафрейма - ту, которую будем проверять на аномальность
    day_ago_ts = current_ts - pd.DateOffset(days=1)  # достаем такую же 15-минутку сутки назад

    current_value = df[df['ts'] == current_ts][metric].iloc[0] # достаем из датафрейма значение метрики в максимальную 15-минутку
    day_ago_value = df[df['ts'] == day_ago_ts][metric].iloc[0] # достаем из датафрейма значение метрики в такую же 15-минутку сутки назад

    # вычисляем отклонение
    if current_value <= day_ago_value:
        diff = abs(current_value / day_ago_value - 1)
    else:
        diff = abs(day_ago_value / current_value - 1)

    # проверяем больше ли отклонение метрики заданного порога threshold
    # если отклонение больше, то вернем 1, в противном случае 0
    if diff > threshold:
        is_alert = 1
    else:
        is_alert = 0

    return is_alert, current_value, diff


def check_anomaly_iqr(df, metric, alpha=0.1):
    # функция check_anomaly_iqr предлагает алгоритм проверки значения на аномальность посредством
    # построения доверительного интервала на основе значений метрики за час до и после такой же 15-минутки вчера,
    # доверительный интервал строится с помощью межквартильного размаха
    current_ts = df[
        'ts'].max()  # достаем максимальную 15-минутку из датафрейма - ту, которую будем проверять на аномальность
    day_ago_ts = current_ts - pd.DateOffset(days=1)  # достаем такую же 15-минутку сутки назад

    current_value = df[df['ts'] == current_ts][metric].iloc[
        0]  # достаем из датафрейма значение метрики в максимальную 15-минутку
    day_ago_value = df[df['ts'] == day_ago_ts][metric].iloc[
        0]  # достаем из датафрейма значение метрики в такую же 15-минутку сутки назад
    index_yesterday = df[df['ts'] == day_ago_ts][metric].index[
        0]  # достаем индекс значения метрики в такую же 15-минутку сутки назад

    # считаем квантили и межквартильный размах по значениям метрики за час до и после такой же 15-минутки вчера
    q25 = df[metric][index_yesterday - 4:index_yesterday + 5].quantile(0.25)
    q75 = df[metric][index_yesterday - 4:index_yesterday + 5].quantile(0.75)
    IQR = q75 - q25

    # вычисляем отклонение
    if current_value <= day_ago_value:
        diff = abs(current_value / day_ago_value - 1)
    else:
        diff = abs(day_ago_value / current_value - 1)

    # проверяем попадание в доверительный интервал
    # если не попадает, то вернем 1, в противном случае 0
    if current_value > q75 + alpha * IQR or current_value < q25 - alpha * IQR:
        is_alert = 1
    else:
        is_alert = 0
    return is_alert, current_value, diff


def check_anomaly_prophet(df, metric):
    # функция check_anomaly_iqr предлагает алгоритм проверки значения на аномальность посредством
    # использования библиотеки prophet и построения прогнозного значения и доверительного интервала
    # для последней 15-минутки на основе данных за последние 14 дней + сегодня
    current_ts = df[
        'ts'].max()  # достаем максимальную 15-минутку из датафрейма - ту, которую будем проверять на аномальность
    day_ago_ts = current_ts - pd.DateOffset(days=1)  # достаем такую же 15-минутку сутки назад
    current_value = df[df['ts'] == current_ts][metric].iloc[
        0]  # достаем из датафрейма значение метрики в максимальную 15-минутку
    day_ago_value = df[df['ts'] == day_ago_ts][metric].iloc[
        0]  # достаем из датафрейма значение метрики в такую же 15-минутку сутки назад

    # создаем вспомогательный датафрейм для прогноза
    df1 = pd.DataFrame()
    df1['ds'] = df['ts']
    df1['y'] = df[metric]

    # задаем количество прогнозируемых значений
    predictions = 1

    # создаем тренировочный датафрейм
    train_df = df1[:-predictions]
    train_df.columns = ['ds', 'y']

    # тренируем модель
    m = Prophet()
    m.fit(train_df)

    # делаем предсказание
    future = m.make_future_dataframe(periods=predictions, freq='15 min')
    forecast = m.predict(future)

    # объединяем датафрейм с прогнозами и реальными значениями
    cmp_df = forecast.set_index('ds')[['yhat', 'yhat_lower', 'yhat_upper']].join(df1.set_index('ds'))

    # вычисляем ошибки
    cmp_df['e'] = cmp_df['y'] - cmp_df['yhat']
    cmp_df['p'] = 100 * cmp_df['e'] / cmp_df['y']
    # вычисляем относительную ошибку прогноза для последней 15-минутки
    relative_forecast_error = round(abs(cmp_df[-predictions:]['p']), 2)[0]

    # вычисляем отклонение
    if current_value <= day_ago_value:
        diff = abs(current_value / day_ago_value - 1)
    else:
        diff = abs(day_ago_value / current_value - 1)

    # проверяем попадание в доверительный интервал прогноза
    # если не попадает, то вернем 1, в противном случае 0
    if current_value > cmp_df['yhat_upper'][-1:][0] or current_value < cmp_df['yhat_lower'][-1:][0]:
        is_alert = 1
    else:
        is_alert = 0
    return is_alert, current_value, diff, relative_forecast_error


def run_alerts(df, chat):
    chat_id = chat
    bot = telegram.Bot(token='')

    data = df

    metrics = ['DAU_feed', 'likes', 'views', 'CTR', 'DAU_messages', 'messages_sent']
    for metric in metrics:
        # проверяем метрику на аномальность алгоритмом, описаным внутри функции check_anomaly_prophet()
        is_alert, current_value, diff, error = check_anomaly_prophet(data,
                                                                     metric)
        if is_alert:
            msg = '''Метрика {metric}:\nтекущее значение = {current_value:.2f} \nотклонение от вчера {diff:.2%}\nотносительная ошибка прогноза {error}% \nдашборд: https://superset.lab.karpov.courses/superset/dashboard/1551/''' \
                .format(metric=metric,
                        current_value=current_value,
                        diff=diff,
                        error=error)

            sns.set(rc={'figure.figsize': (16, 10)})  # задаем размер графика
            plt.tight_layout()

            date_str = datetime.date.today() - datetime.timedelta(days=1)
            date_str = date_str.strftime('%Y-%m-%d')
            data_today_and_yesterday = data[data['date'] >= date_str]

            ax = sns.lineplot(  # строим линейный график
                data=data_today_and_yesterday.sort_values(by=['date', 'hm']),  # задаем датафрейм для графика
                x="hm", y=metric,  # указываем названия колонок в датафрейме для x и y
                hue="date"
                # задаем "группировку" на графике, т е хотим чтобы для каждого значения date была своя линия построена
            )

            for ind, label in enumerate(
                    ax.get_xticklabels()):  # этот цикл нужен чтобы разрядить подписи координат по оси Х,
                if ind % 15 == 0:
                    label.set_visible(True)
                else:
                    label.set_visible(False)
            ax.set(xlabel='time')  # задаем имя оси Х
            ax.set(ylabel=metric)  # задаем имя оси У

            ax.set_title('{}'.format(metric))  # задае заголовок графика
            ax.set(ylim=(0, None))  # задаем лимит для оси У

            # формируем файловый объект
            plot_object = io.BytesIO()
            ax.figure.savefig(plot_object)
            plot_object.seek(0)
            plot_object.name = '{0}.png'.format(metric)
            plt.close()

            # отправляем алерт
            bot.sendMessage(chat_id=chat_id, text=msg)
            bot.sendPhoto(chat_id=chat_id, photo=plot_object)

data = get_data()
run_alerts(data, chat)
