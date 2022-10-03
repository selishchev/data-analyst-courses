# coding=utf-8

from datetime import datetime, timedelta
import pandas as pd
from io import StringIO
import requests
import pandahouse as ph

from airflow.decorators import dag, task
from airflow.operators.python import get_current_context


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
    'start_date': datetime(2022, 9, 5),
}

# Интервал запуска DAG
schedule_interval = '@daily'

@dag(default_args=default_args, schedule_interval=schedule_interval, catchup=False)
def dag_feed_and_messages():
    @task()
    def extract_feed():
        # Для каждого юзера посчитаем число просмотров и лайков контента
        query = """select toDate(time) as date, user_id, countIf(action = 'view') as views, countIf(action = 'like') 
                    as likes, age, gender, os
                    from simulator_20220820.feed_actions
                    where toDate(time) = today() - 1
                    group by date, user_id, age, gender, os
                    format TSVWithNames"""
        df_cube_feed = ch_get_df(query=query)
        return df_cube_feed

    @task()
    def extract_messages():
        # Для каждого юзера считаем, сколько он получает и отсылает сообщений, скольким людям он пишет, сколько людей пишут ему
        query = """
                select date, user_id, messages_received, messages_sent, users_received, users_sent, age, gender, os from
                (select toDate(time) as date, user_id, count(reciever_id) as messages_sent, count(distinct reciever_id) 
                as users_sent, age, gender, os
                from simulator_20220820.message_actions
                where toDate(time) = today() - 1
                group by date, user_id, age, gender, os) t2
                left join
                (select toDate(time) as date, reciever_id, count(user_id) as messages_received, count(distinct user_id) 
                as users_received, age, gender, os
                from simulator_20220820.message_actions 
                where toDate(time) = today() - 1
                group by date, reciever_id, age, gender, os) t3
                on t2.user_id = t3.reciever_id
                group by date, user_id, messages_received, messages_sent, users_received, users_sent, age, gender, os
                format TSVWithNames
                """
        df_cube_messages = ch_get_df(query=query)
        return df_cube_messages

    @task
    def transform_join(df_cube_feed, df_cube_messages):
        # Объединяем две таблицы в одну
        df_joined_cube = df_cube_feed.join(df_cube_messages.set_index('user_id'), on='user_id', rsuffix='_messages') \
                                    .drop(['date_messages', 'os_messages', 'age_messages', 'gender_messages'], axis=1) \
                                    .rename(columns={"date": "event_date"}) \
                                    .fillna(0)
        return df_joined_cube

    @task
    def transform_os(df_joined_cube):
        # Считаем все эти метрики в разрезе по полу, возрасту и ОС
        df_cube_os = df_joined_cube.drop(['user_id', 'age', 'gender'], axis=1) \
                                    .groupby(['event_date', 'os']) \
                                    .sum() \
                                    .astype('int32')
        return df_cube_os

    @task
    def transform_gender(df_joined_cube):
        df_cube_gender = df_joined_cube.drop(['user_id', 'age', 'os'], axis=1) \
                                        .groupby(['event_date', 'gender']) \
                                        .sum() \
                                        .astype('int32')
        return df_cube_gender

    @task
    def transform_age(df_joined_cube):
        df_cube_age = df_joined_cube.drop(['user_id', 'os', 'gender'], axis=1) \
                                    .groupby(['event_date', 'age']) \
                                    .sum() \
                                    .astype('int32')
        return df_cube_age

    @task
    def transform_concat(df_cube_os, df_cube_gender, df_cube_age):
        # преобразуем данные
        df_cube_concat = pd.concat([df_cube_os, df_cube_gender, df_cube_age],
                                    keys=['os', 'gender', 'age'],
                                    names=['dimension', 'event_date', 'dimension_value']) \
                            .swaplevel(0,1) \
                            .reset_index()
        return df_cube_concat

    @task
    def load(df_cube_concat):
        # финальные данные со всеми метриками записываем в отдельную таблицу в ClickHouse
        connection = {'host': 'https://clickhouse.lab.karpov.courses',
                      'database': 'test',
                      'user': 'student-rw',
                      'password': '656e2b0c9c'}

        df_cube_concat['event_date'] = pd.to_datetime(df_cube_concat['event_date'])
        df_cube_concat = df_cube_concat.astype(
            {'dimension': 'object', 'dimension_value': 'object', 'likes': int, 'views': int, 'messages_received': int, \
             'messages_sent': int, 'users_received': int, 'users_sent': int})
        ph.to_clickhouse(df_cube_concat, table='DAG_selishchev1', index=False, connection=connection)

    df_cube_feed = extract_feed()
    df_cube_messages = extract_messages()
    df_joined_cube = transform_join(df_cube_feed, df_cube_messages)
    df_cube_os = transform_os(df_joined_cube)
    df_cube_gender = transform_gender(df_joined_cube)
    df_cube_age = transform_age(df_joined_cube)
    df_cube_concat = transform_concat(df_cube_os, df_cube_gender, df_cube_age)
    load(df_cube_concat)


dag_feed_and_messages = dag_feed_and_messages()
