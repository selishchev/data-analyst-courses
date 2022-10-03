# coding=utf-8

from datetime import datetime, timedelta
import pandas as pd
from io import StringIO
import requests
import telegram
import matplotlib.pyplot as plt
import seaborn as sns
import io

from airflow.decorators import dag, task


# Функция для CH
def ch_get_df(query='Select 1', host='https://clickhouse.lab.karpov.courses', user='student', password='dpo_python_2020'):
    r = requests.post(host, data=query.encode("utf-8"), auth=(user, password), verify=False)
    result = pd.read_csv(StringIO(r.text), sep='\t')
    return result


# Дефолтные параметры, которые прокидываются в таски
default_args = {
    'owner': 'a.selishchev',
    'depends_on_past': False,
    'retries': 2,
    'retry_delay': timedelta(minutes=5),
    'start_date': datetime(2022, 9, 8),
}

# Интервал запуска DAG
schedule_interval = '0 11 * * *'

@dag(default_args=default_args, schedule_interval=schedule_interval, catchup=False)
def dag_full_report_selischev():
    # Достаем данные по ленте новостей
    @task()
    def extract_feed_metrics():
        query = """select toDate(time) as __timestamp,
                            count(distinct user_id) as DAU,
                            countIf(action='view') / DAU as views_per_user,
                              countIf(action = 'like') / countIf(action='view') as CTR
                              from simulator_20220820.feed_actions
                        where toDate(time) between today() - 30 and today()
                        group by __timestamp
                        format TSVWithNames"""
        df_cube_feed = ch_get_df(query=query)
        df_cube_feed['__timestamp'] = pd.to_datetime(df_cube_feed['__timestamp'])
        return df_cube_feed

    # Достаем данные по сервису сообщений
    @task()
    def extract_messages_metrics():
        query = """select toDate(time) as __timestamp,
                                count(distinct user_id) as DAU,
                                count(user_id) as messages_sent,
                                messages_sent / count(distinct user_id) as messages_per_user
                                  from simulator_20220820.message_actions
                            where toDate(time) between today() - 30 and today()
                            group by __timestamp
                            format TSVWithNames"""
        df_cube_messages = ch_get_df(query=query)
        df_cube_messages['__timestamp'] = pd.to_datetime(df_cube_messages['__timestamp'])
        return df_cube_messages

    # Достаем данные по метрике "stickiness" для ленты новостей
    @task()
    def extract_feed_stickiness():
        query = """SELECT toStartOfDay(toDateTime(date)) AS __timestamp,
                        max(stickiness) AS "Stickiness (%)"
                        FROM
                        (select DISTINCT date, DAU/MAU * 100 as stickiness
                        from
                        (select toDate(time) as date,
                        count(DISTINCT user_id) over (
                        order by date range between 29 preceding and current row) as MAU,
                        count(DISTINCT user_id) over (partition by date) as DAU
                        from simulator_20220820.feed_actions)) AS virtual_table
                        GROUP BY toStartOfDay(toDateTime(date))
                        HAVING __timestamp between today() - 30 and today()
                        ORDER BY __timestamp
                        format TSVWithNames"""
        df_cube_feed_stickiness = ch_get_df(query=query)
        df_cube_feed_stickiness['__timestamp'] = pd.to_datetime(df_cube_feed_stickiness['__timestamp'])
        return df_cube_feed_stickiness

    # Достаем данные по метрике "stickiness" для сервиса сообщений
    @task()
    def extract_msg_stickiness():
        query = """SELECT toStartOfDay(toDateTime(date)) AS __timestamp,
                    max(stickiness) AS "Stickiness (%)"
                    FROM
                    (select DISTINCT date, DAU/MAU * 100 as stickiness
                    from
                    (select toDate(time) as date,
                    count(DISTINCT user_id) over (
                    order by date range between 29 preceding and current row) as MAU,
                    count(DISTINCT user_id) over (partition by date) as DAU
                    from simulator_20220820.message_actions)) AS virtual_table
                    GROUP BY toStartOfDay(toDateTime(date))
                    HAVING __timestamp between today() - 30 and today()
                    ORDER BY __timestamp
                    format TSVWithNames"""
        df_cube_msg_stickiness = ch_get_df(query=query)
        df_cube_msg_stickiness['__timestamp'] = pd.to_datetime(df_cube_msg_stickiness['__timestamp'])
        return df_cube_msg_stickiness

    # Достаем данные по пользователям, которые используют и ленту новостей, и сервис сообщений
    @task()
    def extract_feed_and_messages():
        query = """
                SELECT toStartOfDay(toDateTime(day)) AS __timestamp,
                       max(DAU) AS DAU
                FROM
                  (select toStartOfDay(toDateTime(time)) as day,
                          count(DISTINCT user_id) as DAU
                   from simulator_20220820.feed_actions
                   join simulator_20220820.message_actions using(user_id)
                   group by day
                   order by day) AS virtual_table
                GROUP BY toStartOfDay(toDateTime(day))
                HAVING __timestamp between today() - 30 and today()
                ORDER BY __timestamp
                format TSVWithNames
                """
        df_cube_feed_and_messages = ch_get_df(query=query)
        df_cube_feed_and_messages['__timestamp'] = pd.to_datetime(df_cube_feed_and_messages['__timestamp'])
        return df_cube_feed_and_messages

    # Достаем данные по пользователям, которые используют только ленту новостей
    @task()
    def extract_feed_only():
        query = """
                    SELECT toStartOfDay(toDateTime(day)) AS __timestamp,
                           max(DAU) AS DAU
                    FROM
                      (select toStartOfDay(toDateTime(time)) as day,
                              count(DISTINCT user_id) as DAU
                       from
                         (select *
                          from simulator_20220820.feed_actions
                          where user_id not in
                              (select user_id
                               from simulator_20220820.message_actions))
                       group by day
                       order by day) AS virtual_table
                    GROUP BY toStartOfDay(toDateTime(day))
                    HAVING __timestamp between today() - 30 and today()
                    ORDER BY __timestamp
                    format TSVWithNames
                    """
        df_cube_feed_only = ch_get_df(query=query)
        df_cube_feed_only['__timestamp'] = pd.to_datetime(df_cube_feed_only['__timestamp'])
        return df_cube_feed_only

    # Собираем и отправляем отчет
    @task
    def load(df_cube_feed, df_cube_messages, df_cube_feed_stickiness,
             df_cube_msg_stickiness, df_cube_feed_and_messages, df_cube_feed_only):
        my_token = '5436973473:AAF7l7a6zBvOikkt0i3ov82Vdz9ny8gVnts'
        bot = telegram.Bot(token=my_token)
        chat_id = -555114317
        # Составляем и отправляем сообщения
        msg_feed = 'Report on feed ' + str(df_cube_feed['__timestamp'][len(df_cube_feed['__timestamp']) - 1])[:10] + \
              ' (yesterday)/' + str(df_cube_feed['__timestamp'][len(df_cube_feed['__timestamp']) - 7])[:10] + \
              ' (week ago)/' + str(df_cube_feed['__timestamp'][0])[:10] + \
              ' (month ago):\nDAU: ' + str(df_cube_feed['DAU'][len(df_cube_feed['__timestamp']) - 1]) + '/' + \
              str(df_cube_feed['DAU'][len(df_cube_feed['DAU']) - 7]) + '/' + str(df_cube_feed['DAU'][0]) + \
              '\nViews_per_user: ' + str(round(df_cube_feed['views_per_user'][len(df_cube_feed['__timestamp']) - 1], 3)) + \
              '/' + str(round(df_cube_feed['views_per_user'][len(df_cube_feed['__timestamp']) - 7], 3)) + '/' + \
              str(round(df_cube_feed['views_per_user'][0], 3)) + \
              '\nCTR: ' + str(round(df_cube_feed['CTR'][len(df_cube_feed['__timestamp']) - 1], 3)) + '/' + \
              str(round(df_cube_feed['CTR'][len(df_cube_feed['__timestamp']) - 7], 3)) + '/' + \
              str(round(df_cube_feed['CTR'][0], 3))  + '\nStickiness (%): ' + \
              str(round(df_cube_feed_stickiness['Stickiness (%)'][len(df_cube_feed['__timestamp']) - 1], 2)) + '/' + \
              str(round(df_cube_feed_stickiness['Stickiness (%)'][len(df_cube_feed['__timestamp']) - 7], 2)) + '/' + \
              str(round(df_cube_feed_stickiness['Stickiness (%)'][0], 2))
        bot.sendMessage(chat_id=chat_id, text=msg_feed)

        msg_messages = 'Report on messages ' + str(df_cube_feed['__timestamp'][len(df_cube_feed['__timestamp']) - 1])[:10] + \
                   ' (yesterday)/' + str(df_cube_feed['__timestamp'][len(df_cube_feed['__timestamp']) - 7])[:10] + \
                   ' (week ago)/' + str(df_cube_feed['__timestamp'][0])[:10] + \
                   ' (month ago):\nDAU (users who sent messages): ' + \
                   str(df_cube_messages['DAU'][len(df_cube_feed['__timestamp']) - 1]) + '/' + \
                   str(df_cube_messages['DAU'][len(df_cube_feed['DAU']) - 7]) + '/' + str(df_cube_messages['DAU'][0]) + \
                   '\nMessages sent: ' + str(
            round(df_cube_messages['messages_sent'][len(df_cube_feed['__timestamp']) - 1], 3)) + \
                   '/' + str(round(df_cube_messages['messages_sent'][len(df_cube_feed['__timestamp']) - 7], 3)) + '/' + \
                   str(round(df_cube_messages['messages_sent'][0], 3)) + \
                   '\nMessages per user: ' + \
                   str(round(df_cube_messages['messages_per_user'][len(df_cube_feed['__timestamp']) - 1], 3)) + '/' + \
                   str(round(df_cube_messages['messages_per_user'][len(df_cube_feed['__timestamp']) - 7], 3)) + '/' + \
                   str(round(df_cube_messages['messages_per_user'][0], 3)) + '\nStickiness (%): ' + \
                   str(round(df_cube_msg_stickiness['Stickiness (%)'][len(df_cube_feed['__timestamp']) - 1],
                             2)) + '/' + \
                   str(round(df_cube_msg_stickiness['Stickiness (%)'][len(df_cube_feed['__timestamp']) - 7],
                             2)) + '/' + \
                   str(round(df_cube_msg_stickiness['Stickiness (%)'][0], 2))
        bot.sendMessage(chat_id=chat_id, text=msg_messages)

        msg_feed_and_messages = 'Report on feed and messages ' + \
                        str(df_cube_feed['__timestamp'][len(df_cube_feed['__timestamp']) - 1])[:10] + \
                       ' (yesterday)/' + str(df_cube_feed['__timestamp'][len(df_cube_feed['__timestamp']) - 7])[:10] + \
                       ' (week ago)/' + str(df_cube_feed['__timestamp'][0])[:10] + \
                       ' (month ago):\nDAU (users who used feed AND messages): ' + \
                       str(df_cube_feed_and_messages['DAU'][len(df_cube_feed['__timestamp']) - 1]) + '/' + \
                       str(df_cube_feed_and_messages['DAU'][len(df_cube_feed['DAU']) - 7]) + '/' + str(
            df_cube_feed_and_messages['DAU'][0]) + \
                       '\nDAU (users who used only feed): ' + str(
            round(df_cube_feed_only['DAU'][len(df_cube_feed['__timestamp']) - 1], 3)) + \
                       '/' + str(round(df_cube_feed_only['DAU'][len(df_cube_feed['__timestamp']) - 7], 3)) + '/' + \
                       str(round(df_cube_feed_only['DAU'][0], 3))
        bot.sendMessage(chat_id=chat_id, text=msg_feed_and_messages)

        # Составляем и отправляем графики
        fig, axes = plt.subplots(2, 2, figsize=(25, 10))
        fig.subplots_adjust(hspace=0.3)
        sns.lineplot(ax=axes[0, 0], data=df_cube_feed, x='__timestamp', y='DAU')
        axes[0, 0].set_xlabel('date')
        axes[0, 0].set_title('DAU last month (feed)', fontsize=14, fontweight="bold")
        sns.lineplot(ax=axes[0, 1], data=df_cube_feed, x='__timestamp', y='views_per_user')
        axes[0, 1].set_xlabel('date')
        axes[0, 1].set_title('Views per user last month (feed)', fontsize=14, fontweight="bold")
        sns.lineplot(ax=axes[1, 0], data=df_cube_feed, x='__timestamp', y='CTR')
        axes[1, 0].set_xlabel('date')
        axes[1, 0].set_title('CTR last month (feed)', fontsize=14, fontweight="bold")
        sns.lineplot(ax=axes[1, 1], data=df_cube_feed_stickiness, x='__timestamp', y='Stickiness (%)')
        axes[1, 1].set_xlabel('date')
        axes[1, 1].set_title('Stickiness (%) last month (feed)', fontsize=14, fontweight="bold")
        plot_object = io.BytesIO()
        plt.savefig(plot_object)
        plot_object.seek(0)
        plot_object.name = 'plotline.png'
        plt.close()
        bot.sendPhoto(chat_id=chat_id, photo=plot_object)

        fig, axes = plt.subplots(2, 2, figsize=(25, 10))
        fig.subplots_adjust(hspace=0.3)
        sns.lineplot(ax=axes[0, 0], data=df_cube_messages, x='__timestamp', y='DAU')
        axes[0, 0].set_xlabel('date')
        axes[0, 0].set_title('DAU last month (messages)', fontsize=14, fontweight="bold")
        sns.lineplot(ax=axes[0, 1], data=df_cube_messages, x='__timestamp', y='messages_sent')
        axes[0, 1].set_xlabel('date')
        axes[0, 1].set_title('Messages sent last month', fontsize=14, fontweight="bold")
        sns.lineplot(ax=axes[1, 0], data=df_cube_messages, x='__timestamp', y='messages_per_user')
        axes[1, 0].set_xlabel('date')
        axes[1, 0].set_title('Messages per user last month', fontsize=14, fontweight="bold")
        sns.lineplot(ax=axes[1, 1], data=df_cube_msg_stickiness, x='__timestamp', y='Stickiness (%)')
        axes[1, 1].set_xlabel('date')
        axes[1, 1].set_title('Stickiness (%) last month (messages)', fontsize=14, fontweight="bold")
        plot_object = io.BytesIO()
        plt.savefig(plot_object)
        plot_object.seek(0)
        plot_object.name = 'plotline.png'
        plt.close()
        bot.sendPhoto(chat_id=chat_id, photo=plot_object)

        fig, axes = plt.subplots(2, figsize=(25, 10))
        fig.subplots_adjust(hspace=0.3)
        sns.lineplot(ax=axes[0], data=df_cube_feed_and_messages, x='__timestamp', y='DAU')
        axes[0].set_xlabel('date')
        axes[0].set_title('DAU last month (users who used feed AND messages)', fontsize=14, fontweight="bold")
        sns.lineplot(ax=axes[1], data=df_cube_feed_only, x='__timestamp', y='DAU')
        axes[1].set_xlabel('date')
        axes[1].set_title('DAU last month (users who used only feed)', fontsize=14, fontweight="bold")
        plot_object = io.BytesIO()
        plt.savefig(plot_object)
        plot_object.seek(0)
        plot_object.name = 'plotline.png'
        plt.close()
        bot.sendPhoto(chat_id=chat_id, photo=plot_object)

    df_cube_feed = extract_feed_metrics()
    df_cube_messages = extract_messages_metrics()
    df_cube_feed_stickiness = extract_feed_stickiness()
    df_cube_msg_stickiness = extract_msg_stickiness()
    df_cube_feed_and_messages = extract_feed_and_messages()
    df_cube_feed_only = extract_feed_only()
    load(df_cube_feed, df_cube_messages, df_cube_feed_stickiness, df_cube_msg_stickiness,
         df_cube_feed_and_messages, df_cube_feed_only)


dag_full_report_selischev = dag_full_report_selischev()
