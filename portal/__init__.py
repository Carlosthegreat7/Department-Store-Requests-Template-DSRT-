# RR 07-31-2023
import os
from flask import Flask, flash, redirect, url_for
from functools import update_wrapper
from flask import session



def loggedin_required():
    def decorator(fn):
        def wrapped_function(*args, **kwargs):
            if 'sdr_loggedin' not in session or not session['sdr_loggedin']:
                flash("Please login to access this page")
                return redirect(url_for('index',_external=True,))
            return fn(*args, **kwargs)
        return update_wrapper(wrapped_function, fn)
    return decorator


def require_role(role_code):
    def decorator(fn):
        def wrapped_function(*args, **kwargs):
            if 'sdr_loggedin' not in session or not session['sdr_loggedin']:
                return redirect(url_for('index',_external=True,))
            if role_code+";" not in session['sdr_curr_user_role']:
                flash("You are not authorized to access this page")
                return redirect(url_for('index',_external=True,))
            return fn(*args, **kwargs)
        return update_wrapper(wrapped_function, fn)
    return decorator


def require_type(dept_code):
    def decorator(fn):
        def wrapped_function(*args, **kwargs):
            if 'sdr_loggedin' not in session or not session['sdr_loggedin']:
                return redirect(url_for('index',_external=True,))
            if dept_code not in session['sdr_usertype']:
                flash("You are not authorized to access this page")
                return redirect(url_for('index',_external=True,))
            return fn(*args, **kwargs)
        return update_wrapper(wrapped_function, fn)
    return decorator


# create the flask app
app = Flask(__name__)


app.config['SECRET_KEY']        = 'n3wtr3nds!'

app.config['UPLOAD_FOLDER'] = os.path.abspath(os.path.dirname(__file__))+'/uploads'


app.config['LDAP_PROVIDER_URL'] = 'ldap://MGSVR01.mgroup.local/'
app.config['ATC_NAV']           = 'DRIVER={SQL Server};SERVER=MGSVR14.mgroup.local;DATABASE=ATCREP;UID=nav;trusted_connection=yes;READONLY=True;'
app.config['NIC_NAV']           ='DRIVER={SQL Server};SERVER=MGSVR14.mgroup.local;DATABASE=nicrep;UID=nav;trusted_connection=yes;READONLY=True;'
app.config['MIS_SysDev']        = 'DRIVER={SQL Server};SERVER=MGSVR14.mgroup.local;DATABASE=MIS_SysDev;UID=nicportal;PWD=n1cp0rtal;READONLY=True;'


app.config['NIC_NAV_connect'] = 'NICREP'
app.config['ATC_NAV_connect'] = 'ATCREP'
app.config['TPI_NAV_connect'] = 'TPIREP'
app.config['NIC_NAV_Live_connect'] = 'NIC'
app.config['ATC_NAV_Live_connect'] = 'ATC'
app.config['TPI_NAV_Live_connect'] = 'TPI'
app.config['MIS_SysDev_connect'] = 'MIS_SysDev'



# from portal.Admin.views import admin_blueprint
# app.register_blueprint(admin_blueprint, url_prefix='/Admin')

# from portal.SDREntries.views import sdr_blueprint
# app.register_blueprint(sdr_blueprint, url_prefix='/SDREntries')

# from portal.Approval.views import approval_blueprint
# app.register_blueprint(approval_blueprint, url_prefix='/Approval')

# from portal.Reports.views import reports_blueprint
# app.register_blueprint(reports_blueprint, url_prefix='/Reports')
