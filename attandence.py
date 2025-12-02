import json
import os
import datetime

files = os.listdir("data")
student_absents = {}
with open("students.json", "r") as f:
    students = json.load(f)
for i in students['names']:
    student_absents[i] = 0
def get_date_month():
    now = datetime.datetime.now()
    start_month = now.strftime("%Y-%m-01")
    return now.strftime("%Y-%m-%d"), start_month

def all_time(files, student_absents):
    for file in files:
        with open(f"data/{file}", "r") as f:
            data = json.load(f)
        for i in data:
            if data[i]['status'] == 'absent':
                student_absents[i] += 1
    return student_absents

def this_month(files, student_absents):
    end_month, start_month = get_date_month()

    for file in files:
        file_date = file.split("_")[1].replace(".json", "")

        if not (start_month <= file_date <= end_month):
            continue

        with open(f"data/{file}", "r") as f:
            data = json.load(f)

        for i in data:
            if data[i]['status'] == 'absent':
                student_absents[i] += 1

    return student_absents


res = this_month(files, student_absents)
print(sorted(res.items(), key=lambda x: x[1], reverse=True))
