import mysql.connector
from datetime import datetime
import MetaTrader5 as mt5  # Assuming you're using MetaTrader5 for data fetching

# Database Utility Class


class MySQLDatabase:
    def __init__(self, host, user, password, database):
        self.host = host
        self.user = user
        self.password = password
        self.database = database
        self.connection = self.connect()
        self.cursor = self.connection.cursor()

    def connect(self):
        return mysql.connector.connect(
            host=self.host,
            user=self.user,
            password=self.password,
            database=self.database
        )

    def create_table(self, table_name, schema):
        create_table_query = f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
                {schema}
        );
        """
        self.cursor.execute(create_table_query)

    def insert_row(self, table_name, columns, values):
        placeholders = ", ".join(["%s"] * len(values))
        columns_string = ", ".join(columns)
        insert_query = f"""
        INSERT INTO {table_name} ({columns_string})
        VALUES ({placeholders})
        """
        self.cursor.execute(insert_query, values)

    def delete_oldest_rows(self, table_name, max_rows):
        count_query = f"SELECT COUNT(*) FROM {table_name}"
        self.cursor.execute(count_query)
        row_count = self.cursor.fetchone()[0]

        if row_count > max_rows:
            delete_query = f"""
            DELETE FROM {table_name}
            ORDER BY id ASC
            LIMIT {row_count - max_rows}
            """
            self.cursor.execute(delete_query)
            self.connection.commit()

    def commit(self):
        self.connection.commit()

    def close(self):
        self.cursor.close()
        self.connection.close()

# MetaTrader5 Data Fetcher


class MetaTrader5Data:
    @staticmethod
    def fetch_candles(symbol, timeframe, num_candles):
        if not mt5.initialize():
            raise Exception("MetaTrader5 initialization failed")

        rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, num_candles)
        mt5.shutdown()

        candles = []
        for rate in rates:
            candles.append({
                "timestamp": datetime.fromtimestamp(rate['time']),
                "open_price": rate['open'],
                "high_price": rate['high'],
                "low_price": rate['low'],
                "close_price": rate['close'],
                "volume": rate['tick_volume']
            })
        return candles

# Reusable Candle Data Saver
    def save_candle_data(db, table_name, candles, max_rows=200):
        # Create the table if it doesn't exist
        schema = """
            id INT AUTO_INCREMENT PRIMARY KEY,
            symbol VARCHAR(10),
            timestamp DATETIME,
            open_price FLOAT,
            high_price FLOAT,
            low_price FLOAT,
            close_price FLOAT,
            volume FLOAT
        """
        db.create_table(table_name, schema)

        # Insert candles and maintain row limit
        for candle in candles:
            db.insert_row(
                table_name,
                ["symbol", "timestamp", "open_price", "high_price",
                    "low_price", "close_price", "volume"],
                [
                    "USDCHF",  # Example symbol, update dynamically if needed
                    candle["timestamp"],
                    candle["open_price"],
                    candle["high_price"],
                    candle["low_price"],
                    candle["close_price"],
                    candle["volume"]
                ]
            )
            db.delete_oldest_rows(table_name, max_rows)
        db.commit()


"""# Main Program
if __name__ == "__main__":
    # Database connection configuration
    db = MySQLDatabase(
            host="localhost",
            user="your_username",  # Replace with your MySQL username
            password="your_password",  # Replace with your MySQL password
            database="trading_data"  # Replace with your database name
    )

    try:
            # Fetch candle data
            candles = MetaTrader5Data.fetch_candles("USDCHF", mt5.TIMEFRAME_M1, 200)

            # Save candle data to a table
            save_candle_data(db, "usdchf_candles", candles, max_rows=200)
            print("Candle data successfully saved!")
    except Exception as e:
            print(f"Error: {e}")
    finally:
            db.close()
"""
