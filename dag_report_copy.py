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
def dag_report_selischev():
    @task()
    def extract_yesterday_metrics():
        # Достаем метрики за вчерашний день
        query = """select toDate(time) as date,
                        count(distinct user_id) as DAU,
                          countIf(action = 'like') as likes, 
                          countIf(action='view') as views, 
                          likes / views as CTR
                          from simulator_20220820.feed_actions 
                    where toDate(time) = today() - 1
                    group by date
                    format TSVWithNames"""
        df_cube_yesterday = ch_get_df(query=query)
        return df_cube_yesterday

    @task()
    def extract_week_metrics():
        # Достаем метрики за неделю
        query = """
                select toDate(time) as date,
                    count(distinct user_id) as DAU,
                    countIf(action = 'like') as likes, 
                    countIf(action='view') as views, 
                    likes / views as CTR
                    from simulator_20220820.feed_actions 
                    where toDate(time) between today() - 7 and today() - 1
                    group by date
                format TSVWithNames
                """
        df_cube_week = ch_get_df(query=query)
        df_cube_week['date'] = pd.to_datetime(df_cube_week['date'])
        return df_cube_week

    @task
    def load(df_cube_yesterday, df_cube_week):
        # Отправляем отчет
        my_token = '5436973473:AAF7l7a6zBvOikkt0i3ov82Vdz9ny8gVnts'
        bot = telegram.Bot(token=my_token)
        chat_id = -555114317
        msg = 'Report on ' + str(df_cube_yesterday['date'][0])[:10] + '\nDAU: ' + str(df_cube_yesterday['DAU'][0]) + \
              '\nLikes: ' + str(df_cube_yesterday['likes'][0]) + '\nViews: ' + str(df_cube_yesterday['views'][0]) + \
              '\nCTR: ' + str(round(df_cube_yesterday['CTR'][0], 3))
        bot.sendMessage(chat_id=chat_id, text=msg)

        sns.set(rc={'figure.figsize': (11.7, 8.27)})
        sns.lineplot(data=df_cube_week['DAU'], x=df_cube_week['date'], y=df_cube_week['DAU'])
        plt.title('DAU')
        plot_object = io.BytesIO()
        plt.savefig(plot_object)
        plot_object.seek(0)
        plot_object.name = 'DAU_plot.png'
        plt.close()
        bot.sendPhoto(chat_id=chat_id, photo=plot_object)
        sns.lineplot(data=df_cube_week['likes'], x=df_cube_week['date'], y=df_cube_week['likes'])
        plt.title('likes')
        plot_object = io.BytesIO()
        plt.savefig(plot_object)
        plot_object.seek(0)
        plot_object.name = 'likes_plot.png'
        plt.close()
        bot.sendPhoto(chat_id=chat_id, photo=plot_object)
        sns.lineplot(data=df_cube_week['views'], x=df_cube_week['date'], y=df_cube_week['views'])
        plt.title('views')
        plot_object = io.BytesIO()
        plt.savefig(plot_object)
        plot_object.seek(0)
        plot_object.name = 'views_plot.png'
        plt.close()
        bot.sendPhoto(chat_id=chat_id, photo=plot_object)
        sns.lineplot(data=df_cube_week['CTR'], x=df_cube_week['date'], y=df_cube_week['CTR'])
        plt.title('CTR')
        plot_object = io.BytesIO()
        plt.savefig(plot_object)
        plot_object.seek(0)
        plot_object.name = 'CTR_plot.png'
        plt.close()
        bot.sendPhoto(chat_id=chat_id, photo=plot_object)

    df_cube_yesterday = extract_yesterday_metrics()
    df_cube_week = extract_week_metrics()
    load(df_cube_yesterday, df_cube_week)


dag_report_selischev = dag_report_selischev()
