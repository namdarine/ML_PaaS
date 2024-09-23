import pandas as pd
import numpy as np
from sklearn.preprocessing import StandardScaler
from dotenv import load_dotenv
import os
import boto3
from botocore.exceptions import ClientError
from pyspark.sql import SparkSession
from pyspark.sql.functions import col, lower
from pyspark.ml.feature import Imputer, StringIndexer, StandardScaler, VectorAssembler
from pyspark.sql.types import DoubleType, FloatType, IntegerType, LongType
from pyspark.sql import functions as F

load_dotenv()
spark = SparkSession.builder.appName("DataPreprocessing").getOrCreate()

S3_BUCKET_NAME = os.getenv('S3_BUCKET_NAME')
AWS_ACCESS_KEY_ID = os.getenv('AWS_ACCESS_KEY_ID')
AWS_SECRET_ACCESS_KEY = os.getenv('AWS_SECRET_ACCESS_KEY')

s3 = boto3.client(
    's3',
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY
)

# Load dataset file
def load_file(file_key):
    file_name = file_key.split('/')[-1]
    file_path = f"uploaded/{file_name}"
    
    # Check if the file exists in S3 bucket
    try:
        s3.head_object(Bucket=S3_BUCKET_NAME, Key=file_path)
    except ClientError as e:
        if e.response['Error']['Code'] == '404':
            raise FileNotFoundError(f"File '{file_name}' does not exist in S3 bucket '{S3_BUCKET_NAME}'")
        else:
            raise e
    
    # Download the file to a temporary directory
    temp_file_path = f"/tmp/{file_name}"
    with open(temp_file_path, 'wb') as f:
        s3.download_fileobj(S3_BUCKET_NAME, f'uploaded/{file_name}', f)
    
    # Determine file extension
    file_extension = file_name.split('.')[-1]
    
    # Read the file based on its extension
    if file_extension == 'csv':
        return spark.read.csv(temp_file_path)
    elif file_extension == 'xlsx':
        return pd.read_excel(temp_file_path)
    elif file_extension == 'json':
        return spark.read.json(temp_file_path)
    else:
        raise ValueError("Unsupported file format. Supported formats are .csv, .xlsx, and .json")

def preprocessing_data(data):
    data = data.na.drop(how='all')

    for col_name in data.columns:
        try:
            data = data.withColumn(col_name, col(col_name).cast(DoubleType()))
        except Exception as e:
            print(f"Error converting column {col_name}: {e}")

    numeric_cols = [field.name for field in data.schema.fields if isinstance(field.dataType, (DoubleType, FloatType, IntegerType, LongType))]
    imputer = Imputer(strategy="mean", inputCols=numeric_cols, outputCols=[f"{c}_imputed" for c in numeric_cols])

    try:
        df_imputed = imputer.fit(data).transform(data)
        imputed_columns = []
        for c in numeric_cols:
            imputed_col = f"{c}_imputed"
            if imputed_col in df_imputed.columns:
                imputed_columns.append(F.col(imputed_col).alias(c))
            else:
                imputed_columns.append(F.col(c))

        non_numeric_cols = [F.col(c) for c in data.columns if c not in numeric_cols]

        data = df_imputed.select(imputed_columns + non_numeric_cols)
        
    except Exception as e:
        print("Error during imputation:", e)

    #scaled_data = scale_df(data, numeric_cols)
    
    gender_mapping = {}
    
    if 'sex' in data.columns:
        data, gender_mapping = process_gender_column(data, 'sex')
    elif 'gender' in data.columns:
        data, gender_mapping = process_gender_column(data, 'gender')

    return data, gender_mapping

def process_gender_column(data, column_name):
    data = data.withColumn(column_name, lower(col(column_name)))
    indexer = StringIndexer(inputCol=column_name, outputCol=f'{column_name}_index')
    try:
        model = indexer.fit(data)
        data = model.transform(data).drop(column_name)
        gender_mapping = {value: index for index, value in enumerate(model.labels)}
        return data, gender_mapping
    except Exception as e:
        print(f"Error processing '{column_name}' column: {e}")
        return data, {}

def scale_df(data, numeric_cols):
    for col_name in numeric_cols:
        # Assemble the column into a vector
        assembler = VectorAssembler(inputCols=[col_name], outputCol=f"{col_name}_vector")
        data = assembler.transform(data)

        # Scale the vectorized column
        scaler = StandardScaler(inputCol=f"{col_name}_vector", outputCol=f"{col_name}_scaled_vector")
        scaler_model = scaler.fit(data)
        data = scaler_model.transform(data)

        # Extract the scaled value from the vector and replace the original column
        data = data.withColumn(col_name, F.col(f"{col_name}_scaled_vector")[0])
        
        # Drop intermediate vector columns
        data = data.drop(f"{col_name}_vector", f"{col_name}_scaled_vector")

    return data