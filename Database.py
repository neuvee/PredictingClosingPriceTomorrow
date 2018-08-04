import bs4 as bs
import requests
import mysql.connector
from mysql.connector import MySQLConnection, Error
import AlphaVantageWrapper as AVW
import datetime
import time
import random
import math
import numpy as np
import pickle
import Finance
import multiprocessing


# Modified version of method from https://pythonprogramming.net/sp500-company-list-python-programming-for-finance/
def getSP500Tickers():
    resp = requests.get('http://en.wikipedia.org/wiki/List_of_S%26P_500_companies')
    soup = bs.BeautifulSoup(resp.text, 'lxml')
    table = soup.find('table', {'class': 'wikitable sortable'})
    tickersNSectors = []
    for row in table.findAll('tr')[1:]:
        ticker = row.findAll('td')[0].text
        sector = row.findAll('td')[3].text
        tickersNSectors.append((ticker, sector))
    return tickersNSectors


def pointToDate(point):
    dateComps = str(point).split('-')
    date = datetime.date(int(dateComps[0]), int(dateComps[1]), int(dateComps[2]))
    return date


def addFieldsToInsertQuery(query, fields):
    query = query.split(' ')
    query[2] = query[2][0:-1]
    query[3] = query[3][0:-1]
    for field in fields:
        query[2] += "," + field
        query[3] += ",%s"
    query[2] += ")"
    query[3] += ")"
    finalQuery = query[0]
    for i in range(1, len(query)):
        finalQuery += " " + query[i]
    return finalQuery


class DBManager:
    def __init__(self, apiKey, pwrd, n_jobs=None):
        self.av = AVW.AlphaVantage(apiKey)
        self.pwrd = pwrd
        # There should be no unecessary spaces in the string below (e.g. when listing columns), as this will break addFieldsToInsertQuery
        self.insertAllTSDQuery = "INSERT INTO timeseriesdaily(ticker,date,dateTmrw,open,high,low,close,adjClose,volume,adjClosePChange,pDiffClose5SMA,pDiffClose8SMA,pDiffClose13SMA,rsi,pDiffCloseUpperBB,pDiffCloseLowerBB,pDiff20SMAAbsBB,pDiff5SMA8SMA,pDiff5SMA13SMA,pDiff8SMA13SMA,macdHist,deltaMacdHist,stochPK,stochPD,adx,pDiffPdiNdi,obvGrad20,adjCloseGrad20,obvGrad35,adjCloseGrad35,obvGrad50,adjCloseGrad50) " \
                                 "VALUES(%s,DATE(%s),DATE(%s),%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
        if n_jobs is None:
            if multiprocessing.cpu_count() - 2 > 0:
                self.jobs = multiprocessing.cpu_count() - 2
            else:
                self.jobs = 1
        else:
            self.jobs = n_jobs

    def insert(self, query, args, many=False):
        try:
            conn = mysql.connector.connect(host='127.0.0.1', database='stocks', user='root', password=self.pwrd)
            cursor = conn.cursor()
            if many:
                # If this fails you may need to increase the size of max_allowed_packet in the my.ini file for the
                # server
                if len(args) > 100000:
                    # Implemented this as I found that if the insertion had more than 100k args it failed
                    print('Beginning batch insertion into the database...')
                    batchNo = 1
                    for i in range(100000, len(args), 100000):
                        print('Inserting %s to %s' % (i - 100000, i))
                        cursor.executemany(query, args[i - 100000: i])
                        conn.commit()
                        batchNo += 1
                    if i < len(args) - 1:
                        print('Inserting %s to %s' % (i, len(args)))
                        cursor.executemany(query, args[i: len(args)])
                        conn.commit()
                else:
                    cursor.executemany(query, args)
                    conn.commit()
            else:
                cursor.execute(query, args)
                conn.commit()
        except Error as e:
            print(e)
        finally:
            cursor.close()
            conn.close()

    def select(self, query, args):
        try:
            conn = mysql.connector.connect(host='localhost', database='stocks', user='root', password=self.pwrd)
            cursor = conn.cursor()
            cursor.execute(query, args)
            return cursor.fetchall()
        except Error as e:
            print(e)
        finally:
            cursor.close()
            conn.close()

    def formClassBands(self, noOfClasses):
        adjClosePChanges = self.select("SELECT adjClosePChange FROM timeseriesdaily ORDER BY adjClosePChange ASC", ())
        bandSize = math.floor(len(adjClosePChanges) / noOfClasses)
        bands = []
        for i in range(1, noOfClasses):
            bands.append(adjClosePChanges[i * bandSize])
        return bands

    # There was no way around formatting the string to add the field name to the sql query, so I made this method to
    # ensure the injected field name is not malicious
    def getSafeName(self, noOfClasses, trainingPc, testPc, validationPc):
        if validationPc != 0:
            return "`%s_%s_%s_%s`" % (int(noOfClasses), int(trainingPc), int(testPc), int(validationPc))
        else:
            return "`%s_%s_%s`" % (int(noOfClasses), int(trainingPc), int(testPc))

    def determineShortestMember(self, existingVals, bandData, bandNo, shortest, offset):
        if len(existingVals) == 1 and (len(bandData[bandNo]) < shortest):
            shortest = len(bandData[bandNo])
        elif len(existingVals) != 1 and (len(bandData[bandNo]) + existingVals[bandNo + offset]) < shortest:
            shortest = len(bandData[bandNo]) + existingVals[bandNo + offset]
        return shortest

    def updateSetMembers(self, classBands, trainingPc, testPc, validationPc=0):
        noOfClasses = len(classBands) + 1
        name = self.getSafeName(noOfClasses, trainingPc, testPc, validationPc)
        column_names = self.select(
            "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA='stocks' AND TABLE_NAME='timeseriesdaily'",
            ())
        for i in range(0, len(column_names)):
            column_names[i] = column_names[i][0]
        if name[1:-1] not in column_names:
            self.insert(
                "ALTER TABLE timeseriesdaily ADD %s INT NULL;" % self.getSafeName(noOfClasses, trainingPc, testPc,
                                                                                  validationPc),
                ())
        existingVals = self.select(
            "SELECT COUNT(*) FROM timeseriesdaily GROUP BY {0} ORDER BY {0} ASC".format(
                self.getSafeName(noOfClasses,
                                 trainingPc, testPc,
                                 validationPc)), ())
        if (validationPc == 0 and len(existingVals) == 2 * noOfClasses + 1) or (
                validationPc != 0 and len(existingVals) == 3 * noOfClasses + 1):
            offset = 1
        else:
            offset = 0
        if len(existingVals) != 1:
            for i in range(0, noOfClasses):
                existingVals[i + offset] = int(existingVals[i + offset][0]) + int(
                    existingVals[i + offset + noOfClasses][0])
                if validationPc != 0:
                    existingVals[i + offset] += int(existingVals[i + offset + noOfClasses * 2][0])
        bandNo = 0
        bandData = []
        shortest = float('inf')
        print("Fetching data from the database, %.2f%% complete." % (bandNo * 100 / noOfClasses))
        bandData.append(self.select(
            "SELECT t1.ticker,t1.date FROM timeseriesdaily AS t1 "
            "INNER JOIN timeseriesdaily AS t2 "
            "WHERE t2.adjClosePChange < %s "
            "AND t1.ticker = t2.ticker "
            "AND t1.dateTmrw = t2.date "
            "AND t1.{0} IS NULL".format(self.getSafeName(noOfClasses, trainingPc, testPc, validationPc)),
            (classBands[0],)))
        shortest = self.determineShortestMember(existingVals, bandData, bandNo, shortest, offset)
        bandNo += 1
        print("Fetching data from the database, %.2f%% complete." % (bandNo * 100 / noOfClasses))
        while bandNo < len(classBands):
            bandData.append(
                self.select(
                    "SELECT t1.ticker,t1.date FROM timeseriesdaily AS t1 "
                    "INNER JOIN timeseriesdaily AS t2 "
                    "WHERE t2.adjClosePChange >= %s "
                    "AND t2.adjClosePChange < %s "
                    "AND t1.ticker = t2.ticker "
                    "AND t1.dateTmrw = t2.date "
                    "AND t1.{0} IS NULL".format(name),
                    (classBands[bandNo - 1], classBands[bandNo])))
            shortest = self.determineShortestMember(existingVals, bandData, bandNo, shortest, offset)
            bandNo += 1
            print("Fetching data from the database, %.2f%% complete." % (bandNo * 100 / noOfClasses))
        bandData.append(self.select(
            "SELECT t1.ticker,t1.date FROM timeseriesdaily AS t1 "
            "INNER JOIN timeseriesdaily AS t2 "
            "WHERE t2.adjClosePChange >= %s "
            "AND t1.ticker = t2.ticker "
            "AND t1.dateTmrw = t2.date "
            "AND t1.{0} IS NULL".format(self.getSafeName(noOfClasses, trainingPc, testPc, validationPc)),
            (classBands[-1],)))
        shortest = self.determineShortestMember(existingVals, bandData, bandNo, shortest, offset)
        bandNo += 1
        print("Fetching data from the database, %.2f%% complete." % (bandNo * 100 / noOfClasses))
        args = []
        classNo = 0
        print('Determining classes...')
        argsDict = {}
        goals = []
        if len(existingVals) == 1:
            goal = shortest
        for band in bandData:
            random.shuffle(band)
            hdt = 0
            argsDict[classNo] = []
            argsDict[classNo + noOfClasses] = []
            if len(existingVals) != 1:
                goal = shortest - existingVals[classNo + offset]
                goals.append(goal)
            while goal - hdt >= 99:
                for pc in range(0, 100):
                    if pc < trainingPc:
                        args.append((classNo, band[hdt + pc][0], band[hdt + pc][1]))
                        argsDict[classNo].append((classNo, band[hdt + pc][0], band[hdt + pc][1]))
                    elif pc < trainingPc + testPc:
                        args.append((classNo + noOfClasses, band[hdt + pc][0], band[hdt + pc][1]))
                        argsDict[classNo + noOfClasses].append((classNo, band[hdt + pc][0], band[hdt + pc][1]))
                    else:
                        args.append((classNo + (2 * noOfClasses), band[hdt + pc][0], band[hdt + pc][1]))
                hdt += 100
            classNo += 1
        print('Determined classes.')
        query = "UPDATE timeseriesdaily SET {0}=%s WHERE ticker=%s AND date=%s".format(
            self.getSafeName(noOfClasses, trainingPc, testPc, validationPc))
        if len(args) == 1:
            self.insert(query, args[0])
        elif len(args) > 1:
            self.insert(query, args, many=True)
        print('Classes updated for field %s in table timeseriesdaily' % name)

    def getLearningData(self, setFieldName, reqFields, reqNotNulls=[]):
        setInfo = setFieldName.split('_')
        noOfClasses = int(setInfo[0])
        query = "SELECT `%s`" % setFieldName
        for reqField in reqFields:
            query += ", " + reqField
        query += " FROM timeseriesdaily " \
                 "WHERE `{0}` >= %s " \
                 "AND `{0}` <= %s ".format(setFieldName)
        for reqNotNull in reqNotNulls:
            query += "AND " + reqNotNull + " IS NOT NULL "
        print('Getting training data...')
        trainX = np.array(self.select(query, (0, noOfClasses - 1)), np.float32)
        trainY = trainX[:, 0]
        trainX = np.delete(trainX, 0, 1)
        mean = np.mean(trainX, axis=0)
        std = np.std(trainX, axis=0)
        trainX = (trainX - mean) / std
        print('Getting testing data...')
        testX = np.array(self.select(query, (noOfClasses, 2 * noOfClasses - 1)), np.float32)
        testY = testX[:, 0] - noOfClasses
        testX = np.delete(testX, 0, 1)
        testX = (testX - mean) / std
        if len(setInfo) == 3:
            return trainX, trainY, testX, testY
        if len(setInfo) == 4:
            print('Getting validation data...')
            validX = np.array(self.select(query, (2 * noOfClasses, 3 * noOfClasses - 1)))
            validY = validX[:, 0] - 2 * noOfClasses
            validX = np.delete(validX, 0, 1)
            validX = (validX - mean) / std
            return trainX, trainY, testX, testY, validX, validY

    def timeseriesToArgs(self, ticker, points, history, args, lastUpdated=datetime.date.min, fieldsToRestore=None,
                         columnNames=[]):
        maxPeriod = 26
        if lastUpdated == datetime.date.min:
            addingNewStock = True
        else:
            addingNewStock = False
        adjCloseHist = []
        if not addingNewStock:
            query = "SELECT adjClose FROM timeseriesdaily " \
                    "WHERE ticker=%s " \
                    "AND date<=DATE(%s) " \
                    "ORDER BY date DESC LIMIT %s"
            closes = self.select(query, (ticker, lastUpdated, maxPeriod))
            for close in reversed(closes):
                adjCloseHist.append(close[0])
            fc = Finance.FinanceCalculator(seriesSoFar=adjCloseHist[0:14], n_jobs=self.jobs)
            result = self.select("SELECT averageUpward,averageDownward FROM tickers WHERE ticker = %s", (ticker,))
            fc.averageUpward.append(result[0][0])
            fc.averageDownward.append(result[0][1])
        else:
            fc = Finance.FinanceCalculator(n_jobs=self.jobs)
        first = True
        noOfPoints = len(points)
        points = list(reversed(points))
        macdHistBefore = None
        for i in range(0, noOfPoints):
            point = points[i]
            pointInHistory = history.get(point)
            date = pointToDate(point)
            if i < noOfPoints - 1:
                dateTmrw = pointToDate(points[i + 1])
                if dateTmrw == datetime.date.today():
                    dateTmrw = None
            else:
                dateTmrw = None
            # We do not add records to the database that are recorded as today, as the values vary over the day
            if (date - lastUpdated).days >= 0 and (date - datetime.date.today()).days != 0:
                open = float(pointInHistory.get('1. open'))
                high = float(pointInHistory.get('2. high'))
                low = float(pointInHistory.get('3. low'))
                close = float(pointInHistory.get('4. close'))
                adjClose = float(pointInHistory.get('5. adjusted close'))
                volume = int(pointInHistory.get('6. volume'))
                if first:
                    if addingNewStock:
                        adjClosePChange = None
                    else:
                        result = self.select(
                            "SELECT adjClose FROM timeseriesdaily WHERE ticker = %s  ORDER BY date DESC LIMIT 1",
                            (ticker,))
                        adjCloseBefore = result[0][0]
                        adjClosePChange = ((adjClose - adjCloseBefore) / adjCloseBefore) * 100
                        self.insert("UPDATE timeseriesdaily SET dateTmrw=%s WHERE ticker=%s AND dateTmrw IS NULL",
                                    (date, ticker))
                    first = False
                else:
                    adjClosePChange = ((adjClose - adjCloseBefore) / adjCloseBefore) * 100
                adjCloseHist.append(adjClose)
                pDiffClose5SMA = fc.smaPDiff(adjCloseHist, 5)
                pDiffClose8SMA = fc.smaPDiff(adjCloseHist, 8)
                pDiffClose13SMA = fc.smaPDiff(adjCloseHist, 13)
                rsi = fc.RSI(adjCloseHist)
                pDiffCloseUpperBB, pDiffCloseLowerBB, pDiff20SMAAbsBB = fc.bollingerBandsPDiff(adjCloseHist, 20, 2)
                pDiffSMAs = fc.pDiffBetweenSMAs(adjCloseHist, [5, 8, 13])
                pDiff5SMA8SMA = pDiffSMAs[0]
                pDiff5SMA13SMA = pDiffSMAs[1]
                pDiff8SMA13SMA = pDiffSMAs[2]
                _, _, macdHist = fc.MACD(adjCloseHist, slow=12, fast=26)
                if macdHistBefore is not None and macdHist is not None:
                    deltaMacdHist = macdHist - macdHistBefore
                else:
                    deltaMacdHist = None
                fc.updateHighLowClose(high, low, close, adjClose)
                stochPK, stochPD = fc.stochasticOscilator(fastKPeriod=5, slowKPeriod=3)
                pdi, ndi, adx = fc.ADX()
                if pdi is None or ndi is None or ndi == 0:
                    pDiffPdiNdi = None
                else:
                    pDiffPdiNdi = ((pdi - ndi) / ndi) * 100
                obvGrad20, adjCloseGrad20 = fc.OBVAndCloseGradients(volume, 20)
                obvGrad35, adjCloseGrad35 = fc.OBVAndCloseGradients(volume, 35)
                obvGrad50, adjCloseGrad50 = fc.OBVAndCloseGradients(volume, 50)
                arg = [ticker, date, dateTmrw, open, high, low, close, adjClose, volume, adjClosePChange,
                       pDiffClose5SMA, pDiffClose8SMA, pDiffClose13SMA, rsi, pDiffCloseUpperBB, pDiffCloseLowerBB,
                       pDiff20SMAAbsBB, pDiff5SMA8SMA, pDiff5SMA13SMA, pDiff8SMA13SMA, macdHist, deltaMacdHist, stochPK,
                       stochPD, adx, pDiffPdiNdi, obvGrad20, adjCloseGrad20, obvGrad35, adjCloseGrad35, obvGrad50,
                       adjCloseGrad50]
                if fieldsToRestore is not None:
                    for column in columnNames:
                        value = None
                        if fieldsToRestore.get(ticker) is not None:
                            if fieldsToRestore[ticker].get(date) is not None:
                                value = fieldsToRestore[ticker][date].get(column)
                        arg.append(value)
                args.append(tuple(arg))
                adjCloseBefore = adjClose
                macdHistBefore = macdHist
        try:
            averageUpward = fc.averageUpward[-1]
            averageDownward = fc.averageDownward[-1]
            result = self.select("SELECT averageUpward, averageDownward FROM tickers WHERE ticker = %s", (ticker,))
            self.insert(
                "UPDATE tickers SET averageUpward_backup=%s, averageDownward_backup=%s, averageUpward=%s, averageDownward=%s WHERE ticker = %s",
                (result[0][0], result[0][1], averageUpward, averageDownward, ticker))
        except IndexError:
            pass

    def addNewStock(self, ticker, sector, fieldsToRestore=None, readdFromMemory=False, columnNames=[]):
        history = self.av.getDailyHistory(AVW.OutputSize.FULL, ticker)
        points = list(history.keys())
        if readdFromMemory:
            lastUpdated = pointToDate(points[0])
        else:
            lastUpdated = datetime.date.today()
        firstDay = pointToDate(points[-1])
        query = "INSERT INTO tickers(ticker,sector,firstDay,lastUpdated) " \
                "VALUES(%s,%s,DATE(%s),DATE(%s))"
        args = (ticker, sector, firstDay, lastUpdated)
        self.insert(query, args)
        args = []
        self.timeseriesToArgs(ticker, points, history, args, fieldsToRestore=fieldsToRestore,
                              columnNames=columnNames)
        query = self.insertAllTSDQuery
        if fieldsToRestore is not None:
            query = addFieldsToInsertQuery(query, columnNames)
        self.insert(query, args, many=True)
        print('Stock added successfully')

    def addManyNewStocks(self, tickersNSectors, readdFromMemory=False, fieldsToRestore=None, columnNames=[]):
        completed = 0
        timeseriesArgs = []
        start = time.time()
        for (ticker, sector) in tickersNSectors:
            print("Fetching stock data and computing features, %.2f%% complete." % (
                        completed * 100 / len(tickersNSectors)))
            history = self.av.getDailyHistory(AVW.OutputSize.FULL, ticker)
            points = list(history.keys())
            if readdFromMemory:
                lastUpdated = pointToDate(points[0])
            else:
                lastUpdated = datetime.date.today()
            firstDay = pointToDate(points[-1])
            self.insert("INSERT INTO tickers(ticker,sector,firstDay,lastUpdated) VALUES(%s,%s,DATE(%s),DATE(%s))",
                        (ticker, sector, firstDay, lastUpdated))
            self.timeseriesToArgs(ticker, points, history, timeseriesArgs, fieldsToRestore=fieldsToRestore,
                                  columnNames=columnNames)
            completed += 1
            if completed != 0 and completed % 5 == 0 and readdFromMemory is False:
                passed = time.time() - start
                if passed < 60:
                    time.sleep(60 - passed)  # Can only make ~5 request to the API per minute
                start = time.time()
        query = self.insertAllTSDQuery
        if fieldsToRestore is not None:
            query = addFieldsToInsertQuery(query, columnNames)
        if readdFromMemory:
            self.av.localBackup = None  # We do not need the local back up anymore, so we free up the memory
        self.insert(query, timeseriesArgs, many=True)
        print('All stocks added')

    def readdPickledColumns(self, singleStock=False):
        print("Restoring tickers and sectors...")
        if not singleStock:
            with open('tickersNSectors.pickle', 'rb') as handle:
                tickersNSectors = pickle.load(handle)
            self.insert("INSERT INTO tickers(ticker,sector) VALUES (%s,%s)", tickersNSectors, many=True)
        print("Restoring columns in timeseriesdaily...")
        with open('fieldsToRestore.pickle', 'rb') as handle:
            fieldsToRestore = pickle.load(handle)
        args = []
        columns = []
        for ticker in fieldsToRestore.keys():
            for date in fieldsToRestore[ticker].keys():
                arg = [ticker, date]
                if len(columns) == 0:
                    columns = list(fieldsToRestore[ticker][date].keys())
                for column in fieldsToRestore[ticker][date].keys():
                    arg.append(fieldsToRestore[ticker][date][column])
                args.append(tuple(arg))
        query = "INSERT INTO timeseriesdaily(ticker,date"
        endQuery = ") VALUES(%s,DATE(%s)"
        for column in columns:
            query += "," + column
            endQuery += ",%s"
        query += endQuery + ")"
        self.insert(query, args, many=True)
        print("Readded all columns")

    # Make a backup of what we'd usually fetch from the API into memory of av, remember to set the local backup in av to
    # none when done
    def makeLocalBackup(self, storedOnDisk, ticker=None):
        if storedOnDisk is False:
            print("Getting price history from the database...")
            if ticker is None:
                query = "SELECT ticker,date,open,high,low,close,adjClose,volume FROM timeseriesdaily ORDER BY date DESC"
                result = self.select(query, ())
            else:
                query = "SELECT ticker,date,open,high,low,close,adjClose,volume FROM timeseriesdaily WHERE ticker=%s ORDER BY date DESC"
                result = self.select(query, (ticker,))
            priceHistory = {}
            print("Indexing price history...")
            for row in result:
                ticker = row[0]
                date = str(row[1])
                opn = str(row[2])
                high = str(row[3])
                low = str(row[4])
                close = str(row[5])
                adjClose = str(row[6])
                volume = str(row[7])
                if priceHistory.get(ticker) is None:
                    priceHistory[ticker] = {}
                priceHistory[ticker][date] = {'1. open': opn, '2. high': high, '3. low': low, '4. close': close,
                                              '5. adjusted close': adjClose, '6. volume': volume}
            # back it up to disk in case there is an exception when re-adding the prices
            print('Saving backup of price history...')
            with open('priceHistory.pickle', 'wb') as handle:
                pickle.dump(priceHistory, handle, protocol=pickle.HIGHEST_PROTOCOL)
        else:
            print("Getting price history from disk...")
            with open('priceHistory.pickle', 'rb') as handle:
                priceHistory = pickle.load(handle)
        self.av.localBackup = priceHistory

    def readdAllStocks(self, readdFromMemory=True, storedOnDisk=False,
                       columnsToSave=['`4_80_20`', '`2_80_20`', '`4_60_20_20`']):
        # Save a record of tickers and their sectors in case theres an exception when readding the stock
        tickersNSectors = self.select("SELECT ticker,sector FROM tickers", '')
        with open('tickersNSectors.pickle', 'wb') as handle:
            pickle.dump(tickersNSectors, handle, protocol=pickle.HIGHEST_PROTOCOL)
        if readdFromMemory:
            self.makeLocalBackup(storedOnDisk=storedOnDisk)
        print('Getting data to save to disk...')
        # Get all the columns to save in case theres an exception when readding the stock
        query = "SELECT ticker, date"
        for column in columnsToSave:
            query += ", " + column
        query += " FROM timeseriesdaily"
        result = self.select(query, ())
        fieldsToRestore = {}
        # Put the values into a dictionary
        print('Indexing data to save to disk...')
        for row in result:
            if row[0] not in fieldsToRestore.keys():
                fieldsToRestore[row[0]] = {}
            if row[1] not in fieldsToRestore[row[0]].keys():
                fieldsToRestore[row[0]][row[1]] = {}
            for i in range(0, len(columnsToSave)):
                fieldsToRestore[row[0]][row[1]][columnsToSave[i]] = row[i + 2]
        # Save the dictionary to disk
        print('Saving data to disk...')
        with open('fieldsToRestore.pickle', 'wb') as handle:
            pickle.dump(fieldsToRestore, handle, protocol=pickle.HIGHEST_PROTOCOL)
        print('Deleteing old table...')
        self.insert("DELETE FROM tickers", ())
        print('Readding new table along with saved rows')
        raisedException = True
        try:
            self.addManyNewStocks(tickersNSectors, readdFromMemory=readdFromMemory, fieldsToRestore=fieldsToRestore,
                                  columnNames=columnsToSave)
            raisedException = False
        finally:
            self.av.localBackup = None
            if raisedException:
                print(
                    "There was an exception when trying to re-add all stocks, restoring tickers and saved columns to database.\n"
                    "Please make sure to set storedOnDisk to true when re-adding using this method to re-add the database again once the issue is fixed.\n"
                    "Traceback of the exception will appear after the database has been restored.")
                print("Deleting everything added to tickers and timeseriesdaily tables so far...")
                self.insert("DELETE FROM tickers", ())
                print("Readding pickled tickers and columns...")
                self.readdPickledColumns()
                print("Tickers and columns restored, traceback of exception follows.")
                time.sleep(0.1)

    def readdStock(self, ticker, storedOnDisk=False, readdFromMemory=True,
                   columnsToSave=['`4_80_20`', '`2_80_20`', '`4_60_20_20`']):
        sector = self.select("SELECT sector FROM tickers WHERE ticker=%s", (ticker,))[0][0]
        query = "SELECT ticker, date"
        for column in columnsToSave:
            query += ", " + column
        query += " FROM timeseriesdaily WHERE ticker=%s"
        if readdFromMemory:
            self.makeLocalBackup(ticker=ticker, storedOnDisk=storedOnDisk)
        # Get all the columns to save in case theres an exception when readding the stock
        print('Getting data to save to disk...')
        result = self.select(query, (ticker,))
        fieldsToRestore = {}
        print('Indexing data to save to disk...')
        # Put the values into a dictionary
        for row in result:
            if row[0] not in fieldsToRestore.keys():
                fieldsToRestore[row[0]] = {}
            if row[1] not in fieldsToRestore[row[0]].keys():
                fieldsToRestore[row[0]][row[1]] = {}
            for i in range(0, len(columnsToSave)):
                fieldsToRestore[row[0]][row[1]][columnsToSave[i]] = row[i + 2]
        # Save the dictionary to disk
        print('Saving data to disk...')
        with open('fieldsToRestore.pickle', 'wb') as handle:
            pickle.dump(fieldsToRestore, handle, protocol=pickle.HIGHEST_PROTOCOL)
        print('Deleteing ticker from table...')
        self.insert("DELETE FROM tickers WHERE ticker=%s", (ticker,))
        print('Readding stock...')
        raisedException = True
        try:
            self.addNewStock(ticker, sector, fieldsToRestore=fieldsToRestore, columnNames=columnsToSave,
                             readdFromMemory=readdFromMemory)
            raisedException = False
        finally:
            self.av.localBackup = None
            if raisedException:
                print(
                    "There was an exception when trying to re-add all stocks, restoring tickers and saved columns to database.\n"
                    "Please make sure to set storedOnDisk to true when re-adding using this method to re-add the database again once the issue is fixed.\n"
                    "Traceback of the exception will appear after the database has been restored.")
                print("Deleting everything added to tickers and timeseriesdaily tables so far...")
                self.insert("DELETE FROM tickers WHERE ticker=%s", (ticker,))
                print("Readding pickled tickers and columns...")
                self.readdPickledColumns(singleStock=True)
                print("Tickers and columns restored, traceback of exception follows.")
                time.sleep(0.1)

    def updateAllStocks(self):
        tickersNLastUpdated = self.select("SELECT ticker, lastUpdated FROM tickers", '')
        self.updateStocks(tickersNLastUpdated)
        print('All stocks updated')

    def updateStocks(self, tickersNLastUpdated):
        print(
            "WARNING: Some features rely on past calculation (e.g. those based on EMAs) and do not update well, "
            "consider re-adding the stocks instead using the inbuilt methods as this will take the same amount of time "
            "whilst ensuring values for features are consistent.")
        insertArgs = []
        updateArgs = []
        completed = 0
        fetched = 0
        start = time.time()
        for (ticker, lastUpdated) in tickersNLastUpdated:
            print("Fetching stock data, %.2f%% complete." % (completed * 100 / len(tickersNLastUpdated)))
            if (lastUpdated - datetime.date.today()).days != 0:
                # It's neccessary to keep track of today as bugs can occur if an update occurs over 2 days e.g. if it is
                # started at 11:59pm one night and continues into the next day
                today = datetime.date.today()
                updateArgs.append((today, ticker))
                if (today - lastUpdated).days > 100:
                    history = self.av.getDailyHistory(AVW.OutputSize.COMPACT, ticker)
                else:
                    history = self.av.getDailyHistory(AVW.OutputSize.FULL, ticker)
                points = list(history.keys())
                self.timeseriesToArgs(ticker, points, history, insertArgs, lastUpdated)
                fetched += 1
            if fetched != 0 and fetched % 5 == 0:
                passed = time.time() - start
                if passed < 60:
                    time.sleep(60 - passed)  # Can only make ~5 request to the API per minute
                start = time.time()
            completed += 1
        if len(insertArgs) > 1:
            self.insert(self.insertAllTSDQuery, insertArgs, many=True)
        else:
            insertArgs = insertArgs[0]
            self.insert(self.insertAllTSDQuery, insertArgs)
        query = "UPDATE tickers SET lastUpdated = DATE(%s) WHERE ticker = %s;"
        if len(updateArgs) > 1:
            self.insert(query, updateArgs, many=True)
        else:
            updateArgs = updateArgs[0]
            self.insert(query, updateArgs)
        print("100% complete.")
