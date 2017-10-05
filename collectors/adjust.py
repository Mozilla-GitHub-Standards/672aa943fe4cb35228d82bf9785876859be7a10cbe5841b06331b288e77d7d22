import logging
import os
import pyodbc

import click
import pandas as pd

from collectors.main import load_settings

ADJUST_URL_BASE = "https://api.adjust.com/kpis/v1/{app_key}"
logger = logging.getLogger(__name__)


@click.command('adjust')
@click.argument('config_file', type=click.Path(exists=True, readable=True))
def adjust_cmd(config_file) -> None:
    """
    Collect data from adjust.com about DAUS/WAUS/MAUS and Installs

    Example config
    ---
    load_path: /tmp/adjust_daus
    vertica:
      dsn: vertica
      table: adjust_daily_active_users
    adjust:
      token: abcdefg
      apps:
        firefox: abc123
        focus: abc123

    :param dsn: odbc dsn to use for vertica connection, defaults to "vertica"
    :param config_file: configuration file containing credentials and keys for adjust.com
    """

    # Setup
    settings = load_settings(config_file)
    adjust_settings = settings['adjust']
    vertica_settings = settings['vertica']
    load_path = settings['load_path']

    # Grab a vertica connection
    cursor = connect(vertica_settings['dsn'])

    # Collect the data from adjust.com
    output_file = collect(adjust_settings, load_path)

    reject_file = os.path.join(load_path, "rejects")
    exception_file = os.path.join(load_path, "exceptions")
    load(cursor, vertica_settings['table'], output_file, reject_file, exception_file)


def collect(adj_settings, load_path) -> str:
    """
    Collect the data from adjust
    :param adj_settings: dictionary of settings specific to adjust.com
    """
    apps = adj_settings['apps']
    token = adj_settings['token']

    df = merge_apps(apps, token)
    return write_to_file(df, load_path)


def load(cursor, table, data_file, reject_file, exception_file) -> None:
    # Truncate the table before the load since every load is a FULL rewrite
    trunc_tmpl = "TRUNCATE TABLE {tbl}"
    cursor.execute(trunc_tmpl.format(tbl=table))

    files = {
        "data_file": data_file,
        "reject_file": reject_file,
        "exception_file": exception_file
    }

    # Copy the local file
    copy_tmpl = "COPY {table} FROM LOCAL '{data_file}' " \
                "DELIMITER ',' SKIP 1 " \
                "REJECTED DATA '{reject_file}' " \
                "EXCEPTIONS '{exception_file}' " \
                "ABORT ON ERROR DIRECT"
    copy_stmt = copy_tmpl.format(table=table, **files)
    print(copy_stmt)
    cursor.execute(copy_stmt)
    logger.info("Completed loading {table} - #{count} Records".format(
        table=table,
        count=cursor.rowcount
    ))


def merge_apps(apps, adjust_token) -> pd.DataFrame:
    """
    Call collect_app on each of the apps that we are tracking in adjust.com
    :param apps: dict of app names and ids
    :param adjust_token: access token for adjust.com api
    :return:
    """
    frames = []
    for app, key in iter(apps):
        url = build_dau_url(key, adjust_token)
        df = collect_app(app, url)
        frames.append(df)

    df = pd.concat(frames)

    return df


def write_to_file(df, load_path, filename="output.csv") -> str:
    """
    Write a dataframe to local storage
    :param df: data frame containing the activity record
    :param load_path: local path to write the data to
    :param filename: name of the file to save the output to
    :return: fully resolved path to the output file
    """
    data_file = os.path.join(load_path, filename)

    if not os.path.exists(load_path):
        os.makedirs(load_path)

    df.to_csv(data_file, index=False)

    return data_file


def connect(dsn) -> type:
    """
    Obtain an ODBC cursor for vertica
    """
    cnxn = pyodbc.connect("DSN=%s" % dsn)
    logger.info("Database connection established")
    return cnxn.cursor()


def build_dau_url(app_key, token) -> str:
    """
    Build the adjust.com URL for daily activity
    """
    url = ADJUST_URL_BASE.format(app_key=app_key)
    url += ".csv"
    url += "?user_token={user_token}".format(user_token=token)
    url += "&kpis=daus,waus,maus,installs"
    url += "&start_date=2000-01-01"
    url += "&end_date=2030-01-01"
    url += "&grouping=day,os_names"
    url += "&os_names=android,ios"
    return url


def collect_app(app_name, url) -> pd.DataFrame:
    """
    Load the activity CSV for a given app
    :param app_name: Name of the app that this data is for
    :param url: URL where the data can be retrieved
    :return:
    """
    # Get the csv file from adjust and load it into pandas
    col_names = ['adj_date', 'os', 'daus', 'waus', 'maus', 'installs']
    df = pd.read_csv(url, sep=",", header=0, names=col_names)

    # Force convert missing to 0 for installs, this gets us through an unnecessary type conversion
    df['installs'] = df['installs'].fillna(0).astype(int)

    # Append the app name
    df['app'] = app_name
    logging.info("{} collected".format(app_name))

    return df


if __name__ == '__main__':
    adjust_cmd()
