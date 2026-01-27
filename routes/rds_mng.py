from flask import Blueprint, render_template, session, redirect, url_for, flash, request
from portal import loggedin_required
from models import VendorRDS, HierarchyRDS, PricePointRDS, AgeCodeRDS
from extensions import db

# Define the blueprint
rds_mng_bp = Blueprint('rds_mng', __name__)

@rds_mng_bp.route('/admin/management/rds', methods=['GET'])
@loggedin_required()
def admin_management_rds():
    # Security Check
    if session.get('sdr_usertype') != 'Head Office':
        flash("Unauthorized Access")
        return redirect(url_for('index'))
    
    # Fetch all RDS data from the database
    vendors_rds = VendorRDS.query.all()
    hierarchies_rds = HierarchyRDS.query.all()
    price_points_rds = PricePointRDS.query.all()
    age_codes_rds = AgeCodeRDS.query.all()
    
    return render_template('admin_management_rds.html', 
                           vendors=vendors_rds,
                           hierarchies=hierarchies_rds,
                           price_points=price_points_rds,
                           age_codes=age_codes_rds)

@rds_mng_bp.route('/admin/management/rds/add_vendor', methods=['POST'])
@loggedin_required()
def add_vendor_rds():
    if session.get('sdr_usertype') != 'Head Office':
        flash("Unauthorized Access")
        return redirect(url_for('index'))

    company_name = request.form.get('company_name')
    vendor_code = request.form.get('vendor_code')
    mfg_part_no = request.form.get('mfg_part_no')

    if not company_name or not vendor_code or not mfg_part_no:
        flash("Error: All fields are required")
        return redirect(url_for('rds_mng.admin_management_rds'))

    # Check for duplicate vendor code
    existing = VendorRDS.query.filter_by(vendor_code=vendor_code).first()
    if existing:
        flash(f"Error: Vendor Code {vendor_code} already exists")
        return redirect(url_for('rds_mng.admin_management_rds'))

    try:
        new_vendor = VendorRDS(
            company_name=company_name,
            vendor_code=vendor_code,
            mfg_part_no=mfg_part_no
        )
        db.session.add(new_vendor)
        db.session.commit()
        flash("RDS Vendor successfully added", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Database Error: {str(e)}")

    return redirect(url_for('rds_mng.admin_management_rds'))

@rds_mng_bp.route('/admin/management/rds/edit_vendor/<int:id>', methods=['POST'])
@loggedin_required()
def edit_vendor_rds(id):
    if session.get('sdr_usertype') != 'Head Office':
        flash("Unauthorized Access")
        return redirect(url_for('index'))

    vendor = VendorRDS.query.get_or_404(id)
    
    company_name = request.form.get('company_name')
    vendor_code = request.form.get('vendor_code')
    mfg_part_no = request.form.get('mfg_part_no')

    if not company_name or not vendor_code or not mfg_part_no:
        flash("Error: All fields are required")
        return redirect(url_for('rds_mng.admin_management_rds'))

    # Check for duplicate vendor code if it changed
    if vendor.vendor_code != vendor_code:
        existing = VendorRDS.query.filter_by(vendor_code=vendor_code).first()
        if existing:
            flash(f"Error: Vendor Code {vendor_code} already exists")
            return redirect(url_for('rds_mng.admin_management_rds'))

    try:
        vendor.company_name = company_name
        vendor.vendor_code = vendor_code
        vendor.mfg_part_no = mfg_part_no
        
        db.session.commit()
        flash("RDS Vendor successfully updated", "success")
    except Exception as e:
        db.session.rollback()
        flash(f"Database Error: {str(e)}")

    return redirect(url_for('rds_mng.admin_management_rds'))