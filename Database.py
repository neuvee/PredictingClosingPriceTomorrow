import bs4 as bs
import requests
import mysql.connector
from mysql.connector import Error
import AlphaVantageAPI as AVAPI


# Modified version of method from https://pythonprogramming.net/sp500-company-list-python-programming-for-finance/
def getSP500Tickers():
    resp = requests.get('http://en.wikipedia.org/wiki/List_of_S%26P_500_companies')
    soup = bs.BeautifulSoup(resp.text, 'lxml')
    table = soup.find('table', {'class': 'wikitable sortable'})
    tickers = []
    for row in table.findAll('tr')[1:]:
        ticker = row.findAll('td')[0].text
        sector = row.findAll('td')[3].text
        tickers.append((ticker, sector))
    return tickers


class DBManager:
    def __init__(self, apiKey, pwrd):
        self.av = AVAPI.AlphaVantage(apiKey)
        # Connect to the database
        try:
            self.conn = mysql.connector.connect(host='localhost', database='stocks', user='root', password=pwrd)
        except Error as e:
            print(e)

    
